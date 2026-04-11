from protocol.base import create_command
import asyncio
import logging
import socket

logger = logging.getLogger(__name__)

class WifiSerialMower:
    def __init__(self, ip_addr: str, send_port: int, receive_port: int):
        self.ip_addr = ip_addr
        self.send_port = send_port
        self.receive_port = receive_port
        self.sock = None

    async def connect(self):
        """Connect to the mower"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip_addr, self.receive_port))
        self.sock.settimeout(5)  # Set a timeout for receiving data

    async def disconnect(self):
        """Disconnect from the mower"""
        if self.sock:
            self.sock.close()
            self.sock = None

    async def get_response(self, request: bytearray):
        """Send a request and wait for a response"""
        if not self.sock:
            raise ConnectionError("Socket is not connected")

        try:
            self.sock.sendto(request, (self.ip_addr, self.send_port))
        except ConnectionResetError as e:
            logger.error(f"Connection reset while sending data: {e}")
            return None

        try:
            response, _ = self.sock.recvfrom(1024)
            return response
        except socket.timeout:
            logger.warning("No response received (timeout)")
            return None
        except ConnectionResetError as e:
            logger.error(f"Connection reset while receiving data: {e}")
            return None

    async def send_command(self, command_name: str, **kwargs):
        """
        This function is used to simplify the communication of the mower using the commands found in protocol.json.
        It will send a request to the mower and then wait for a response. The response will be parsed and returned to the caller.
        """
        command = create_command(command_name)
        request = command.generate_request(**kwargs)
        response = await self.get_response(request)
        if response is None:
            return None
        

        #if command.validate_response(response) is False:
        #    logger.warning("Response failed validation")

        response_dict = command.parse_response(response)
        response_data = response_dict.get("data", None)
        #response_dict = response
        if (
            response_data is not None and len(response_data) == 1
        ):  # If there is only one key in the response, return the value
            return response_data["response"]
        else:
            return response_data

    async def battery_level(self) -> int | None:
        """Query the mower battery level"""
        return await self.send_command("GetBatteryLevel")
    
    async def serial_number(self) -> int | None:
        """Query the mower battery level"""
        return await self.send_command("GetSerialNumber")
         
         
async def main(mower: WifiSerialMower):
    await mower.connect()

    battery_level = await mower.battery_level()
    print("Battery is: " + str(battery_level) + "%")

    serial_number = await mower.serial_number()
    if serial_number:
        print("Serial Number: " + str(serial_number))
        

    await mower.disconnect()

if __name__ == "__main__":
    #ip_addr = "192.168.1.225"
    ip_addr = "127.0.0.1"
    send_port = 8081
    recive_port = 8080
    mower = WifiSerialMower(ip_addr, send_port, recive_port)

    asyncio.run(main(mower))