import logging
from pyam3.protocol.common import Protocol, crc
from pyam3.protocol.base import commands_class

logger = logging.getLogger(__name__)

# Frame layout (byte offsets from start of frame)
# 02 fd <rem_lo> <rem_hi> [6-byte routing header] <type> <major_lo> <major_hi>
#   <minor_lo> <minor_hi> <len_lo> <len_hi> [payload] <crc> 03
_OFF_STX      = 0
_OFF_MARKER   = 1
_OFF_REM_LO   = 2
_OFF_REM_HI   = 3
_OFF_ROUTING  = 4   # 6-byte routing/sequence header (purpose unknown)
_OFF_TYPE     = 10  # frame type: 0x02 = event
_OFF_MAJOR    = 11  # command major, little-endian 16-bit
_OFF_MINOR    = 13  # command minor, little-endian 16-bit
_OFF_DATA_LEN = 15  # inner data-length field (2 bytes, informational)
_OFF_DATA     = 17  # event payload starts here

_FRAME_TYPE_EVENT = 0x02

# Overhead deducted from "remaining" to get event payload byte count:
# routing(6) + type(1) + major(2) + minor(2) + data_len(2) + CRC(1) + ETX(1) = 15
_PAYLOAD_OVERHEAD = 15

_MIN_FRAME_LEN = _OFF_DATA + 2  # data start + CRC + ETX


class LinkedProtocol(Protocol):

    def __init__(self, command=None):
        if command is not None:
            super().__init__(command)

    def generate_request(self, **kwargs):
        raise NotImplementedError("Not yet implemented")

    def parse_response(self, response: bytearray) -> dict | None:
        """
        Parse a linked-protocol frame.  Returns a dict with 'name' and 'data'
        for known events, or None for unsupported frame types / unknown events.
        Raises ValueError for structurally invalid frames.
        """
        if len(response) < _MIN_FRAME_LEN:
            raise ValueError(f"Frame too short: {len(response)} bytes")

        if response[_OFF_STX] != 0x02 or response[-1] != 0x03:
            raise ValueError("Invalid response format")

        if response[_OFF_MARKER] != 0xFD:
            raise ValueError("Not linked protocol marker")

        remaining = response[_OFF_REM_LO] + (response[_OFF_REM_HI] << 8)
        if remaining != len(response) - 4:
            raise ValueError(
                f"Invalid response length: remaining={remaining}, "
                f"expected {len(response) - 4}"
            )

        if crc(response[1:-2]) != response[-2]:
            raise ValueError("CRC mismatch in response")

        frame_type = response[_OFF_TYPE]
        if frame_type != _FRAME_TYPE_EVENT:
            logger.warning(
                "Unsupported linked frame type 0x%02X — dropping", frame_type
            )
            return None

        major = int.from_bytes(response[_OFF_MAJOR : _OFF_MAJOR + 2], byteorder="little")
        minor = int.from_bytes(response[_OFF_MINOR : _OFF_MINOR + 2], byteorder="little")

        try:
            command = commands_class.get_event_command_from_major_minor(major, minor)
        except ValueError:
            logger.warning(
                "Unknown linked event major=%d minor=%d — dropping", major, minor
            )
            return None

        payload_size = remaining - _PAYLOAD_OVERHEAD
        return self._parse_event(command, response[_OFF_DATA : _OFF_DATA + payload_size])

    def _parse_event(self, command: dict, data: bytearray) -> dict:
        super().__init__(command)
        return {
            "name": self.name,
            "data": self.parse_data(data),
        }
