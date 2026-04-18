from pyam3.protocol.common import Protocol, crc


class SimpleProtocol(Protocol):
    """
    The simple protocol
    """

    def __init__(self, command):
        super().__init__(command)

    def generate_request(self, **kwargs):
        payload_length = self.request_length
        payload_data = self.prepare_payload_data(**kwargs)

        self.request_data = bytearray(6 + payload_length)
        self.request_data[0] = 0x02
        self.request_data[1] = self.major
        self.request_data[2] = payload_length + 1
        self.request_data[3] = self.minor
        if payload_length > 0:
            self.request_data[4 : 4 + payload_length] = payload_data

        self.request_data[-2] = crc(self.request_data[1:-2])
        self.request_data[-1] = 0x03  # ETX

        return self.request_data

    def parse_response(self, response: bytearray) -> dict:
        if response[0] != 0x02 or response[-1] != 0x03:
            raise ValueError("Invalid response format")

        if crc(response[1:-2]) != response[-2]:
            raise ValueError("CRC mismatch in response")

        data_length = response[2] - 1
        if data_length != len(response) - 6:
            raise ValueError("Invalid response length")

        if self.major != response[1]-1:
            raise ValueError(f"Major command mismatch: expected {self.major}, got {response[1]-1}")

        return {
            "status": response[3],
            "data": self.parse_data(response[4:4 + data_length]),
        }
