import socket
import threading
import time
from binascii import hexlify, unhexlify

# Configuration
UDP_IP = "0.0.0.0"
UDP_PORT_OUTPUT = 8080
UDP_PORT_INPUT = 8081
HEARTBEAT_INTERVAL = 3  # seconds
HEARTBEAT_MESSAGE = b'HEARTBEAT'

# Define specific incoming messages and their responses
RESPONSES = {
    unhexlify("0281070000925a010a2f03"): unhexlify("02810b0000925b0500df21c40cbf03"),  # GetSerialNumber
    unhexlify("0281070000927601007e03"): unhexlify("028123000092771d00ced13d006fa63900c45929005f2b040044000092030000a5a639009603"),  # GetAllStatistics
    unhexlify('0281070000900a0114d103'): unhexlify("0281080000900b02005aea03"),  # GetBatteryLevel
}

def send_heartbeats(sock):
    """Send heartbeat messages every HEARTBEAT_INTERVAL seconds."""
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        sock.sendto(HEARTBEAT_MESSAGE, ("<broadcast>", UDP_PORT_OUTPUT))
        print(f"Sent heartbeat: {HEARTBEAT_MESSAGE}")

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((UDP_IP, UDP_PORT_INPUT))
    print(f"UDP server listening on {UDP_IP}:{UDP_PORT_INPUT}")

    # Start the heartbeat thread
    threading.Thread(target=send_heartbeats, args=(sock,), daemon=True).start()

    while True:
        data, addr = sock.recvfrom(1024)
        print(f"Received {hexlify(data)} from {addr}")
        response = RESPONSES.get(data)
        if response:
            sock.sendto(response, addr)
            print(f"Sent {response} to {addr}")

if __name__ == "__main__":
    main()
    