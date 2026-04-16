import asyncio
import logging
from typing import Callable

import serial_asyncio

from client import Mower, _FrameAccumulator

logger = logging.getLogger(__name__)


class _UartProtocol(asyncio.Protocol):
    """
    Private asyncio.Protocol for the UART transport.

    UART is a raw byte stream.  This class accumulates bytes until a complete
    STX…ETX frame can be extracted, then forwards it to Mower._dispatch_frame.
    """

    def __init__(
        self,
        dispatch_frame: Callable[[bytearray], None],
        on_lost: Callable[[Exception | None], None],
    ) -> None:
        self._dispatch_frame = dispatch_frame
        self._on_lost = on_lost
        self._transport: asyncio.Transport | None = None
        self._accumulator = _FrameAccumulator(dispatch_frame)

    def connection_made(self, transport: asyncio.Transport) -> None:
        self._transport = transport

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.error("UART connection lost: %s", exc)
        self._accumulator.reset()
        self._on_lost(exc)
        self._transport = None

    def data_received(self, data: bytes) -> None:
        self._accumulator.feed(data)


# ── UART transport ────────────────────────────────────────────────────────────

class UartMower(Mower):
    """
    Mower client communicating over a USB-to-UART (serial) interface.

    Usage:
        mower = UartMower("/dev/ttyUSB0", baudrate=115200)
        await mower.connect()
        print(await mower.battery_level())
        await mower.disconnect()

    On Windows use a COM port string, e.g. UartMower("COM3").
    """

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self._transport: asyncio.Transport | None = None

    async def connect(self) -> None:
        """Open the serial port and start the async receive loop."""
        loop = asyncio.get_running_loop()
        self._transport, _ = await serial_asyncio.create_serial_connection(
            loop,
            lambda: _UartProtocol(self._dispatch_frame, self._on_transport_lost),
            self.port,
            baudrate=self.baudrate,
        )
        self._connected = True

    async def disconnect(self) -> None:
        """Close the serial port. Cancels any in-flight command futures."""
        if self._transport:
            self._transport.close()
            self._transport = None
        self._connected = False

    def _send_bytes(self, data: bytearray) -> None:
        self._transport.write(bytes(data))

    def _on_transport_lost(self, exc: Exception | None) -> None:
        self._connected = False
        self._cancel_pending()


if __name__ == "__main__":
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

    async def main():
        from client import main as run
        mower = UartMower(port, baudrate)
        await run(mower)

    asyncio.run(main())
