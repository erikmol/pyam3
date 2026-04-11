from protocol.common import Protocol, crc
from protocol.base import commands_class
# This is the linked protocol implementation 


class LinkedProtocol(Protocol):
    """
    The linked protocol
    """

    def __init__(self, command=None):
        if command is not None:
            super().__init__(command)

    def generate_request(self, **kwargs):
        raise NotImplementedError("Not yet implemented")
        
    def parse_response(self, response: bytearray) -> dict:
        """
        Parse the response from the Automower.
        """
        if response[0] != 0x02 or response[-1] != 0x03:
            raise ValueError("Invalid response format")
        
        if response[1] != 0xfd:
            raise ValueError("Not linked protocol marker")

        if response[10] != 0x02:
            raise ValueError("Only events are supported in linked protocol")

        # Extract data
        data_length = response[2] + (response[3] << 8)
        if data_length != len(response) - 4:
            raise ValueError("Invalid response length")

        major = int.from_bytes(response[11:13], byteorder='little')
        minor = int.from_bytes(response[13:15], byteorder='little')

        command = commands_class.get_event_command_from_major_minor(major, minor)
        super().__init__(command)
        
        #if self.major != bytes_to_msg_type(response[5:7]) - 1:
        #    raise ValueError(
        #        f"Major command mismatch: expected {self.major}, got {bytes_to_msg_type(response[5:7]) - 1}"
        #    )

        # Check CRC
        if crc(response[1:-2]) != response[-2]:
            raise ValueError("CRC mismatch in response")

        return {
            "name": self.name,
            "data": self.parse_data(response[17 : 17 + data_length - 15]),
        }

if __name__ == "__main__":
    lp = LinkedProtocol()
    print(
        lp.parse_response(bytearray.fromhex("02fd10000000000001fe02ea1101000100073b03"))
    )
    print(
        lp.parse_response(bytearray.fromhex("02fd10000000000001fe02ea1102000100049703"))
    )
    print(
        lp.parse_response(bytearray.fromhex("02fd10000000000001fe02ea110200010005c903"))
    )
    