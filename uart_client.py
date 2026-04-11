import asyncio
import logging
from typing import Callable

import serial_asyncio

from client import Mower

logger = logging.getLogger(__name__)


# ── UART frame accumulator ────────────────────────────────────────────────────

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
        # Simple protocol — payload length in byte[2]
        return buf[2] + 5


class _UartProtocol(asyncio.Protocol):
    """
    Private asyncio.Protocol for the UART transport.

    UART is a raw byte stream.  This class accumulates bytes until a complete
    STX…ETX frame can be extracted, then forwards it to Mower._dispatch_frame.
    Frame length is determined from the embedded length field, which is more
    robust than scanning for ETX (0x03 can appear in payload data).
    """

    def __init__(
        self,
        dispatch_frame: Callable[[bytearray], None],
        on_lost: Callable[[Exception | None], None],
    ) -> None:
        self._dispatch_frame = dispatch_frame
        self._on_lost = on_lost
        self._transport: asyncio.Transport | None = None
        self._buffer: bytearray = bytearray()

    def connection_made(self, transport: asyncio.Transport) -> None:
        self._transport = transport

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.error("UART connection lost: %s", exc)
        self._buffer.clear()
        self._on_lost(exc)
        self._transport = None

    def data_received(self, data: bytes) -> None:
        self._buffer.extend(data)
        self._extract_frames()

    def _extract_frames(self) -> None:
        """Pull all complete frames out of the buffer and dispatch them."""
        while True:
            # Find the next STX byte — discard anything before it
            try:
                start = self._buffer.index(0x02)
            except ValueError:
                self._buffer.clear()
                return
            if start > 0:
                logger.debug("Discarding %d pre-STX bytes", start)
                del self._buffer[:start]

            # Determine how long this frame should be
            frame_len = _frame_length(self._buffer)
            if frame_len is None:
                return  # need more bytes to read the length field

            if len(self._buffer) < frame_len:
                return  # frame is incomplete — wait for more data

            # Extract the complete frame and hand it to the demultiplexer
            frame = bytearray(self._buffer[:frame_len])
            del self._buffer[:frame_len]
            self._dispatch_frame(frame)


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
