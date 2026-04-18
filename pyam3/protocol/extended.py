import random
from pyam3.protocol.common import Protocol, crc, MowerNotReadyError, MowerCommandError, AMG3_CMD_OK


class ExtendedProtocol(Protocol):
    """
    The extended protocol
    """

    def __init__(self, command):
        super().__init__(command)

    def generate_request(self, **kwargs):
        payload_length = self.request_length
        payload_data = self.prepare_payload_data(**kwargs)

        self.request_data = bytearray(11 + payload_length)
        self.request_data[0] = 0x02  # STX
        self.request_data[1] = 0x81  # Extended protocol

        remaining_bytes = 7 + payload_length
        self.request_data[2] = remaining_bytes & 0xFF
        self.request_data[3] = (remaining_bytes >> 8) & 0xFF

        self.transaction_id = random.randint(1, 255)
        self.request_data[4] = self.transaction_id

        # Major command — big-endian with 0x8000 flag
        self.request_data[5] = ((self.major | 0x8000) >> 8) & 0xFF
        self.request_data[6] = (self.major | 0x8000) & 0xFF

        self.request_data[7] = 1 + payload_length
        self.request_data[8] = self.minor

        if payload_length > 0:
            self.request_data[9 : 9 + payload_length] = payload_data

        self.request_data[-2] = crc(self.request_data[1:-2])
        self.request_data[-1] = 0x03  # ETX

        return self.request_data

    def parse_response(self, response: bytearray) -> dict:
        if response[0] != 0x02 or response[-1] != 0x03:
            raise ValueError("Invalid response format")

        if response[1] != 0x81:
            raise ValueError("Not extended protocol marker")

        if response[5] == 0x7F:
            raise MowerNotReadyError(
                f"Mower not ready (msgType high=0x7F, err=0x{response[6]:02X})"
            )

        data_length = response[2] + (response[3] << 8)
        if data_length != len(response) - 4:
            raise ValueError("Invalid response length, expected {}, got {}".format(data_length, len(response) - 4))

        if self.major != bytes_to_msg_type(response[5:7]) - 1:
            raise ValueError(
                f"Major command mismatch: expected {self.major}, got {bytes_to_msg_type(response[5:7]) - 1}"
            )

        if crc(response[1:-2]) != response[-2]:
            raise ValueError("CRC mismatch in response")

        status = response[8]
        if status != AMG3_CMD_OK:
            raise MowerCommandError(status)

        return {
            "transaction_id": response[4],
            "status": response[8],
            "data": self.parse_data(response[9 : 9 + data_length - 7]),
        }


def bytes_to_msg_type(buffer):
    if len(buffer) != 2:
        raise ValueError("Buffer length must be 2 bytes")
    msg_type = (buffer[0] << 8) | buffer[1]
    return msg_type & 0x7FFF
