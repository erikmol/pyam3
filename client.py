from abc import ABC, abstractmethod
import asyncio
import socket
import logging
from typing import Callable

from protocol.base import create_command
from protocol.extended import ExtendedProtocol
from protocol.linked import LinkedProtocol

logger = logging.getLogger(__name__)

# Largest plausible mower frame in bytes.  A corrupted length field could
# otherwise cause the accumulator to wait indefinitely for data that never
# arrives.  All known mower frames are well under 100 bytes.
MAX_FRAME_SIZE = 512

# Bridge-layer heartbeat: STX + "PING" (not forwarded to mower UART)
_HEARTBEAT_REQUEST  = bytes([0x02, 0x50, 0x49, 0x4E, 0x47, 0x03])  # STX PING ETX
_HEARTBEAT_RESPONSE = bytes([0x02, 0x50, 0x4F, 0x4E, 0x47, 0x03])  # STX PONG ETX
_HEARTBEAT_INTERVAL = 10.0   # seconds between pings
_HEARTBEAT_TIMEOUT  = 5.0    # seconds to wait for a pong


def _frame_length(buf: bytearray) -> int | None:
    """
    Return the expected total byte length of the frame starting at buf[0],
    or None if there aren't enough bytes in buf to determine it yet.

    Frame layouts:
      Extended / Linked  STX(1) marker(1) remaining(2-LE) … CRC(1) ETX(1)
                         total = remaining + 4
      Simple             STX(1) major(1)  payload_len(1)  … CRC(1) ETX(1)
                         total = payload_len + 5
    """
    if len(buf) < 3:
        return None
    marker = buf[1]
    if marker == 0x81 or marker == 0xFD:
        if len(buf) < 4:
            return None
        remaining = buf[2] | (buf[3] << 8)
        return remaining + 4
    else:
        return buf[2] + 5


class _FrameAccumulator:
    """
    Byte-stream to frame extractor, shared by UART and TCP transports.

    Feeds raw bytes in via feed(); emits complete STX…ETX frames to the
    dispatch_frame callback.  Heartbeat frames (PING/PONG) are recognised
    before dispatch and handled separately via the optional pong_callback.
    """

    def __init__(
        self,
        dispatch_frame: Callable[[bytearray], None],
        pong_callback: Callable[[], None] | None = None,
    ) -> None:
        self._dispatch = dispatch_frame
        self._pong_cb = pong_callback
        self._buf: bytearray = bytearray()

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)
        self._extract()

    def reset(self) -> None:
        self._buf.clear()

    def _extract(self) -> None:
        while True:
            try:
                start = self._buf.index(0x02)
            except ValueError:
                self._buf.clear()
                return
            if start > 0:
                logger.debug("Discarding %d pre-STX bytes", start)
                del self._buf[:start]

            frame_len = _frame_length(self._buf)
            if frame_len is None:
                return

            if frame_len > MAX_FRAME_SIZE:
                logger.warning(
                    "Implausible frame length %d (max %d), discarding STX and resyncing",
                    frame_len, MAX_FRAME_SIZE,
                )
                del self._buf[0]
                continue

            if len(self._buf) < frame_len:
                return

            frame = bytearray(self._buf[:frame_len])
            del self._buf[:frame_len]

            # Bridge heartbeat — pong from ESP32, not a mower frame
            if frame == bytearray(_HEARTBEAT_RESPONSE):
                if self._pong_cb:
                    self._pong_cb()
                continue

            self._dispatch(frame)


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


# ── WiFi / TCP transport ──────────────────────────────────────────────────────

class WifiSerialMower(Mower):
    """
    Mower client communicating over WiFi via TCP to an ESP32 bridge.

    The ESP32 acts as a TCP server that forwards frames bidirectionally
    between this client and the mower's UART interface.

    Reconnection is handled automatically with exponential backoff.
    A bridge-layer heartbeat (PING/PONG) runs every HEARTBEAT_INTERVAL
    seconds to detect silent connection loss independently of TCP keepalives.
    """

    def __init__(
        self,
        host: str,
        port: int = 8080,
        *,
        reconnect: bool = True,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._reconnect = reconnect
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._accumulator: _FrameAccumulator | None = None
        self._recv_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._pong_event: asyncio.Event = asyncio.Event()
        self._stop_event: asyncio.Event = asyncio.Event()

    # ── Transport interface ───────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Connect to the ESP32 TCP server and start receive + heartbeat loops.
        Retries with exponential backoff if reconnect=True.
        """
        self._stop_event.clear()
        await self._open_connection()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="wifi-heartbeat"
        )

    async def disconnect(self) -> None:
        """Close the TCP connection and stop background tasks."""
        self._stop_event.set()
        self._reconnect = False
        await self._close_connection()
        for task in (self._heartbeat_task, self._recv_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._heartbeat_task = None
        self._recv_task = None

    def _send_bytes(self, data: bytearray) -> None:
        if self._writer is None:
            raise OSError("Not connected")
        self._writer.write(bytes(data))

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _open_connection(self) -> None:
        """Open one TCP connection with exponential-backoff retries."""
        delay = 1.0
        while not self._stop_event.is_set():
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                sock = writer.get_extra_info("socket")
                if sock is not None:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self._reader = reader
                self._writer = writer
                self._pong_event.clear()
                self._accumulator = _FrameAccumulator(
                    self._dispatch_frame, pong_callback=self._pong_event.set
                )
                self._recv_task = asyncio.create_task(
                    self._recv_loop(), name="wifi-recv"
                )
                self._connected = True
                logger.info("Connected to ESP32 bridge at %s:%d", self.host, self.port)
                return
            except OSError as exc:
                logger.warning(
                    "WiFi connect failed (%s), retrying in %.0fs…", exc, delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _close_connection(self) -> None:
        self._connected = False
        self._cancel_pending()
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        self._reader = None
        if self._accumulator:
            self._accumulator.reset()

    async def _recv_loop(self) -> None:
        """Read bytes from the TCP stream and feed them to the frame accumulator."""
        try:
            while True:
                data = await self._reader.read(4096)
                if not data:
                    logger.warning("ESP32 closed TCP connection")
                    break
                self._accumulator.feed(data)
        except (asyncio.CancelledError, OSError):
            pass
        finally:
            await self._on_connection_lost()

    async def _on_connection_lost(self) -> None:
        self._connected = False
        self._cancel_pending()
        if self._reconnect and not self._stop_event.is_set():
            logger.info("Reconnecting to ESP32 bridge…")
            await self._close_connection()
            await self._open_connection()

    async def _heartbeat_loop(self) -> None:
        """
        Periodically send a PING to the ESP32 bridge and wait for a PONG.
        If the bridge does not respond within HEARTBEAT_TIMEOUT, the connection
        is treated as lost and a reconnect is triggered.
        """
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                if not self._connected:
                    continue
                self._pong_event.clear()
                try:
                    self._send_bytes(bytearray(_HEARTBEAT_REQUEST))
                except OSError:
                    continue
                try:
                    await asyncio.wait_for(
                        self._pong_event.wait(), timeout=_HEARTBEAT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning("Heartbeat timeout — triggering reconnect")
                    await self._on_connection_lost()
        except asyncio.CancelledError:
            pass


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
    host = "192.168.1.100"   # ESP32 bridge IP or hostname
    port = 8080
    mower = WifiSerialMower(host, port)

    asyncio.run(main(mower))
