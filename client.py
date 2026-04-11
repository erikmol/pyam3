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
        self._pending: dict[int, str] = {}  # transaction_id -> command_name

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

    async def get_response(self, request: bytearray, retries: int = 3, retry_delay: float = 0.5):
        """Send a request and wait for a response, retrying on timeout."""
        if not self.sock:
            raise ConnectionError("Socket is not connected")

        for attempt in range(retries):
            try:
                self.sock.sendto(request, (self.ip_addr, self.send_port))
            except ConnectionResetError as e:
                logger.error(f"Connection reset while sending data: {e}")
                return None

            try:
                while True:
                    response, _ = self.sock.recvfrom(1024)
                    # Skip non-protocol packets (e.g. heartbeat broadcasts)
                    if len(response) > 0 and response[0] == 0x02:
                        return response
                    logger.debug(f"Skipping non-protocol packet: {response!r}")
            except socket.timeout:
                if attempt < retries - 1:
                    logger.warning(f"No response (timeout), retrying ({attempt + 1}/{retries - 1})...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.warning("No response received after all retries (timeout)")
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

        tid = command.transaction_id
        if tid is not None:
            self._pending[tid] = command_name

        response = await self.get_response(request)
        if response is None:
            if tid is not None:
                self._pending.pop(tid, None)
            return None

        response_dict = command.parse_response(response)

        if tid is not None:
            resp_tid = response_dict.get("transaction_id")
            if resp_tid is not None and resp_tid != tid:
                logger.warning(
                    f"Transaction ID mismatch for '{command_name}': sent {tid:#04x}, got {resp_tid:#04x}"
                )
            self._pending.pop(tid, None)

        response_data = response_dict.get("data", None)
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