from abc import ABC, abstractmethod
import asyncio
import socket
import logging
from typing import Callable

from protocol.base import create_command
from protocol.extended import ExtendedProtocol
from protocol.linked import LinkedProtocol

logger = logging.getLogger(__name__)


# ── Abstract base ─────────────────────────────────────────────────────────────

class Mower(ABC):
    """
    Transport-agnostic base class for mower clients.

    All protocol logic lives here: command dispatch, transaction ID correlation,
    event callbacks, and retry handling.  Subclasses supply a concrete transport
    by implementing connect(), disconnect(), and _send_bytes().
    """

    def __init__(self) -> None:
        # Extended protocol: transaction_id → Future resolving to raw response bytes
        self._pending: dict[int, asyncio.Future[bytearray]] = {}
        # Simple protocol has no transaction ID — serialise with a lock
        self._simple_lock: asyncio.Lock = asyncio.Lock()
        self._simple_future: asyncio.Future[bytearray] | None = None
        # Linked-protocol event callbacks (registered via on_event)
        self._event_callbacks: list[Callable[[dict], None]] = []
        self._connected: bool = False

    # ── Transport interface ───────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Open the transport and start receiving."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the transport. Should cancel any in-flight futures."""

    @abstractmethod
    def _send_bytes(self, data: bytearray) -> None:
        """Write raw bytes to the underlying transport."""

    # ── Shared demultiplexer ──────────────────────────────────────────────────

    def _dispatch_frame(self, frame: bytearray) -> None:
        """
        Demultiplex a complete STX…ETX frame.
        Called by the transport layer once a full frame is available.
        """
        if len(frame) < 2:
            return

        marker = frame[1]

        # ── Extended protocol response (0x81 marker) ──────────────────────────
        if marker == 0x81:
            if len(frame) < 5:
                logger.debug("Extended frame too short, dropping: %r", frame)
                return
            tid = frame[4]
            fut = self._pending.get(tid)
            if fut is None:
                logger.debug("Unsolicited extended response tid=%d, dropping", tid)
                return
            if not fut.done():
                fut.set_result(frame)

        # ── Linked protocol event (0xfd marker) ───────────────────────────────
        elif marker == 0xFD:
            try:
                event = LinkedProtocol().parse_response(frame)
            except Exception as exc:
                logger.warning("Failed to parse linked event: %s — raw: %r", exc, frame)
                return
            for cb in self._event_callbacks:
                try:
                    cb(event)
                except Exception as exc:
                    logger.warning("Event callback raised: %s", exc)

        # ── Simple protocol response (major byte < 0x80) ──────────────────────
        elif marker < 0x80:
            sf = self._simple_future
            if sf is None:
                logger.debug("Simple response with no pending future, dropping: %r", frame)
                return
            if not sf.done():
                sf.set_result(frame)

        # ── Anything else (heartbeats, unknown markers) ───────────────────────
        else:
            logger.debug("Unknown frame marker=0x%02x, dropping", marker)

    def _cancel_pending(self) -> None:
        """Cancel all in-flight futures. Called by transports on connection loss."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        if self._simple_future is not None and not self._simple_future.done():
            self._simple_future.cancel()
        self._simple_future = None

    # ── Public API ────────────────────────────────────────────────────────────

    def on_event(self, handler: Callable[[dict], None]) -> None:
        """
        Register a callback for linked-protocol events.
        Called whenever the mower sends an unsolicited event (e.g. after
        SubscribeMowerAppEvents or SubscribePowerEvents).

        The handler receives a dict with keys 'name' and 'data'.
        """
        self._event_callbacks.append(handler)

    async def send_command(
        self,
        command_name: str,
        *,
        timeout: float = 5.0,
        retries: int = 3,
        **kwargs,
    ):
        """
        Send a command from commands.json and return its response data.

        Extra keyword arguments are forwarded as request parameters, e.g.:
            await mower.send_command("SetMode", mode=1)

        Returns the parsed response data, or None on timeout / error.
        For commands with a single response field the value is unwrapped directly
        (e.g. GetBatteryLevel returns 85 rather than {"response": 85}).
        """
        if not self._connected:
            logger.error("send_command called before connect()")
            return None

        command = create_command(command_name)
        request = command.generate_request(**kwargs)

        if isinstance(command, ExtendedProtocol):
            raw = await self._send_extended(command, request, timeout=timeout, retries=retries)
        else:
            raw = await self._send_simple(command, request, timeout=timeout, retries=retries)

        if raw is None:
            return None

        try:
            response_dict = command.parse_response(raw)
        except ValueError as exc:
            logger.error("Failed to parse response for '%s': %s", command_name, exc)
            return None

        response_data = response_dict.get("data")
        if response_data is not None and len(response_data) == 1:
            return response_data["response"]
        return response_data

    async def battery_level(self) -> int | None:
        """Query the mower battery level (percent)."""
        return await self.send_command("GetBatteryLevel")

    async def serial_number(self) -> int | None:
        """Query the mower serial number."""
        return await self.send_command("GetSerialNumber")

    # ── Internal send helpers ─────────────────────────────────────────────────

    async def _send_extended(
        self,
        command: ExtendedProtocol,
        request: bytearray,
        *,
        timeout: float,
        retries: int,
    ) -> bytearray | None:
        tid = command.transaction_id
        loop = asyncio.get_running_loop()

        for attempt in range(retries):
            fut: asyncio.Future[bytearray] = loop.create_future()
            self._pending[tid] = fut
            try:
                self._send_bytes(request)
                return await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                if attempt < retries - 1:
                    logger.warning(
                        "Extended tid=%d timeout, retrying (%d/%d)…",
                        tid, attempt + 1, retries - 1,
                    )
                else:
                    logger.warning(
                        "Extended tid=%d no response after %d retries", tid, retries
                    )
            finally:
                self._pending.pop(tid, None)

        return None

    async def _send_simple(
        self,
        command,
        request: bytearray,
        *,
        timeout: float,
        retries: int,
    ) -> bytearray | None:
        loop = asyncio.get_running_loop()

        async with self._simple_lock:
            for attempt in range(retries):
                fut: asyncio.Future[bytearray] = loop.create_future()
                self._simple_future = fut
                try:
                    self._send_bytes(request)
                    return await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError:
                    if attempt < retries - 1:
                        logger.warning(
                            "Simple command timeout, retrying (%d/%d)…",
                            attempt + 1, retries - 1,
                        )
                    else:
                        logger.warning(
                            "Simple command no response after %d retries", retries
                        )
                finally:
                    self._simple_future = None

        return None


# ── WiFi / UDP transport ──────────────────────────────────────────────────────

class _WifiProtocol(asyncio.DatagramProtocol):
    """
    Private asyncio.DatagramProtocol for the WiFi/UDP transport.
    UDP delivers complete datagrams, so frames are passed straight through to
    Mower._dispatch_frame without any buffering.
    """

    def __init__(
        self,
        dispatch_frame: Callable[[bytearray], None],
        on_lost: Callable[[Exception | None], None],
    ) -> None:
        self._dispatch_frame = dispatch_frame
        self._on_lost = on_lost

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        pass  # transport reference held by WifiSerialMower

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.error("UDP connection lost: %s", exc)
        self._on_lost(exc)

    def error_received(self, exc: Exception) -> None:
        # On Windows, ICMP port-unreachable arrives here as ConnectionResetError
        logger.error("UDP transport error: %s", exc)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._dispatch_frame(bytearray(data))


class WifiSerialMower(Mower):
    """Mower client communicating over WiFi via UDP."""

    def __init__(self, ip_addr: str, send_port: int, receive_port: int) -> None:
        super().__init__()
        self.ip_addr = ip_addr
        self.send_port = send_port
        self.receive_port = receive_port
        self._transport: asyncio.DatagramTransport | None = None

    async def connect(self) -> None:
        """Open the UDP socket and start the async receive loop."""
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _WifiProtocol(self._dispatch_frame, self._on_transport_lost),
            local_addr=(self.ip_addr, self.receive_port),
            family=socket.AF_INET,
        )
        self._connected = True

    async def disconnect(self) -> None:
        """Close the UDP socket. Cancels any in-flight command futures."""
        if self._transport:
            self._transport.close()
            self._transport = None
        self._connected = False

    def _send_bytes(self, data: bytearray) -> None:
        self._transport.sendto(bytes(data), (self.ip_addr, self.send_port))

    def _on_transport_lost(self, exc: Exception | None) -> None:
        self._connected = False
        self._cancel_pending()


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(mower: Mower):
    await mower.connect()

    battery_level = await mower.battery_level()
    print("Battery is: " + str(battery_level) + "%")

    serial_number = await mower.serial_number()
    if serial_number:
        print("Serial Number: " + str(serial_number))

    await mower.disconnect()

if __name__ == "__main__":
    ip_addr = "127.0.0.1"
    send_port = 8081
    receive_port = 8080
    mower = WifiSerialMower(ip_addr, send_port, receive_port)

    asyncio.run(main(mower))
