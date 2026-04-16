This is a python library used to communicate with a robotic mower over either a WiFi/UDP link or a USB-to-UART serial connection.
The library sends commands and handles responses across both transports using a shared async client. As the connection can be unstable, the client automatically retries commands that time out.

Various commands such as GetBatteryLevel, GetRemainingChargingTime or SetOverridePark can be sent to the mower. The commands and their responses can be used to understand the state of the mower, and to be able to control it or changing settings.

A command is defined by a tuple of two numbers, called "Message Type" and "Sub Command".

A command can have request parameters, such as the duration for a SetOverridePark command. The command can also have response parameters, such as the battery level from the GetBatteryLevel command.

An example of a command that have both request parameters and response parameters is the GetMessage command, used to read the message log of the mower.
```
"GetMessage": {
    "msgType": 4730,
    "subCmd": 1,
    "requestType": {
        "messageId": "uint32"
    },
    "responseType": {
        "time": "tUnixTime",
        "code": "uint32",
        "severity": "uint8"
    }
}
```

### Installation

```
pip install -r requirements.txt
```

### Quick start

#### WiFi

```python
import asyncio
from client import WifiSerialMower

async def main():
    mower = WifiSerialMower(ip_addr="192.168.1.225", send_port=8081, receive_port=8080)
    await mower.connect()

    print(await mower.battery_level())   # e.g. 85  (percent)
    print(await mower.serial_number())   # e.g. 12345678

    # Any command from commands.json can be sent via send_command()
    state = await mower.send_command("GetState")
    mode  = await mower.send_command("GetMode")

    # Commands with request parameters pass them as keyword arguments
    await mower.send_command("SetMode", mode=1)
    await mower.send_command("SetOverridePark", duration=3600)

    await mower.disconnect()

asyncio.run(main())
```

#### USB / UART

```python
import asyncio
from uart_client import UartMower

async def main():
    mower = UartMower("/dev/ttyUSB0", baudrate=115200)  # Windows: "COM3"
    await mower.connect()

    print(await mower.battery_level())
    await mower.disconnect()

asyncio.run(main())
```

Both transports share the same `send_command`, `battery_level`, and `serial_number` API.
The client retries timed-out requests up to 3 times.

#### Subscribing to events

The mower pushes unsolicited state/activity changes via the linked protocol.
Register a callback with `on_event` before sending a subscribe command:

```python
mower.on_event(lambda event: print(event["name"], event["data"]))
await mower.send_command("SubscribeMowerAppEvents")
# callback fires whenever the mower reports a state or activity change
```

### Bench testing over USB-UART

Two scripts in `scripts/` are provided for function-testing a mower connected via a USB-to-UART adapter, without needing to run the mower.

#### `scripts/bench_test.py`

Exercises all safe read-only commands in sequence and writes a timestamped JSONL log with parsed results and raw frames. The raw frames can be used later to build a response emulator or write regression tests against real hardware.

```
python scripts/bench_test.py --port COM3
python scripts/bench_test.py --port /dev/ttyUSB0 --baud 115200 --interval 10
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | required | Serial port, e.g. `COM3` or `/dev/ttyUSB0` |
| `--baud` | `115200` | Baud rate |
| `--log-dir` | `logs/` | Directory for JSONL log files |
| `--interval` | `0` | Repeat every N seconds; `0` = one-shot |

After connecting the script subscribes to mower events so any unsolicited pushes (state changes, activity changes) also land in the log.

Each log entry is a JSON object on one line:

```jsonc
// Polled command
{"type": "command", "timestamp": "…", "command": "GetBatteryLevel",
 "status": "ok", "result": 72,
 "frames": [{"direction": "tx", "t": "…", "hex": "0281…03"},
             {"direction": "rx", "t": "…", "hex": "0281…03"}]}

// Unsolicited frame (heartbeat, event, unknown)
{"type": "unsolicited", "timestamp": "…", "marker": "0xfd", "hex": "02fd…03"}

// Parsed linked-protocol event (fires alongside the unsolicited entry)
{"type": "event", "timestamp": "…", "name": "StateEvent", "data": {"response": 7}}
```

#### `scripts/event_monitor.py`

Passive listener — subscribes to mower events and logs all unsolicited traffic without sending any polling commands. Useful for observing the mower while it charges or idles on the bench.

```
python scripts/event_monitor.py --port COM3
python scripts/event_monitor.py --port COM3 --duration 120
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | required | Serial port |
| `--baud` | `115200` | Baud rate |
| `--log-dir` | `logs/` | Directory for JSONL log files |
| `--duration` | `0` | Stop after N seconds; `0` = run until Ctrl-C |

#### Frame capture (`scripts/logging_mower.py`)

Both scripts share `LoggingUartMower`, a thin subclass of `UartMower` that intercepts `_dispatch_frame` and `_send_bytes` to record every raw byte exchange. Unsolicited frames — heartbeats, events, or anything with no matching pending future — are captured here too, before the base class silently drops them.

### Testing with the mock mower

`mock_mower.py` runs a local UDP server that mimics a small subset of mower responses (GetSerialNumber, GetAllStatistics, GetBatteryLevel) and broadcasts a heartbeat every 3 seconds. Point the client at `127.0.0.1` to use it:

```
# terminal 1
python mock_mower.py

# terminal 2 — the client's __main__ block already targets 127.0.0.1
python client.py
```

### Simple and extended protocol and linked package types
There are three different protocols available:

1. The simple protocol has been studied before, for example in [wall-e-esp8266-automower](https://gitlab.com/nbrgmn/wall-e-esp8266-automower/-/tree/feature/RestructureProject?ref_type=heads) referencing a german [mikrocontroller.net thread](https://www.mikrocontroller.net/topic/304526?page=single#4403150). 
2. The extended protocol is (as far as I'm aware), not previosly documented extensively online. It is however used in [Husqvarna Research hrp](https://github.com/HusqvarnaResearch/hrp) where I've picked up the structure.
3. The linked package protocol have been studied in the [Automower-BLE](https://github.com/alistair23/AutoMower-BLE) library.  

### Request format: Simple and extended protocol
The encoded bytes follow this format:

1. `0x02`: STX (Start of Text) marker
2. Protocol header:
   - For message types >= 0x7F (extended protocol), 6 bytes:
     - `0x81`: Protocol Extended marker
     - 2 bytes: Number of remaining bytes, in little-endian format.
     - 1 byte: Transaction ID. This is returned in the response so that the application can map the response to the request.
     - 2 bytes: Message Type (major) with high bit set (big-endian)
   - For message types < `0x7F` (simple protocol), one byte:
     - 1 byte: Message Type (major)
3. 1 byte: Payload length
4. N bytes: Payload (including Sub Command (minor))
5. 1 byte: CRC8-maxim checksum
6. `0x03`: ETX (End of Text) marker

#### Example with simple protocol 
For example, `021401014e03` is the request for simple GetBatteryData (20, 1):
```
| 02  | 14      | 01  | 01  | 4e  | 03  |
| STX | MSGTYPE | LEN | PLD | CRC | ETX |
```
#### Example with extended protocol 
For example, `0281070042900a0114d103` is the request for GetBatteryLevel (4106, 20) with a Transaction ID of `0x42` (random per request):
```
| 02  | 81  | 07  00 | 42   | 90  0a  | 01  | 14  | d1  | 03  |
| STX | PEM | NBYTES | T ID | MSGTYPE | LEN | PLD | CRC | ETX |
```

### Response format: Simple and extended protocol
The response format is very similar to the request format, but with an additional status byte prepending the data. Please note that the Sub Command is not returned. Also please note that responses Message Type (major) is one digit higher than the request, meaning that if 20 was the request Message Type, the response would be 21.

As the Sub Command is not returned, we can also (for the extended protocol) send a transaction id equal to the Sub Command, which mean the Sub Command is effecively returned as well.
#### Simple protocol
GetSensorData:
`02150c0000005000eaffef03001101cc03`

```
| 02  | 15      | 0c  | 00     | 00 00 50 00 ea ff ef 03 00 11 01 | cc  | 03  |
| STX | MSGTYPE | LEN | STATUS | DATA                             | CRC | ETX |
```

#### Extended protocol
`02810800039239020000da03`

```
| 02  | 81  | 08 00  | 03   | 92 39   | 02  | 00     | 00   | da  | 03  |
| STX | PEM | NBYTES | T ID | MSGTYPE | LEN | STATUS | DATA | CRC | ETX |
```

### Linked package protocol

Header (9 bytes)
    0x02 (1 byte, constant)
    0xfd (1 byte, constant for linked package type)
    2 bytes length (packet length - 4)
    4 bytes channel id, can be 0.
    1 byte is_linked (so linked packets may not be linked!?)
    1 byte CRC8-MAXIM (bytes with index 1 to 8)

Payload for is_linked == 0x00
    1 byte message type
    variable length payload

Payload for is_linked == 0x01
    1 byte packet type (0x00 = request, 0x01 = response, 0x02 = event)
    For request/response:
        0xaf (1 byte, constant)
    2 bytes message type
    2 byte sub command
    For responses: 
        1 byte result: OK(0), UNKNOWN_ERROR(1), INVALID_VALUE(2), OUT_OF_RANGE(3), NOT_AVAILABLE(4), NOT_ALLOWED(5), INVALID_GROUP(6), INVALID_ID(7), DEVICE_BUSY(8), INVALID_PIN(9), MOWER_BLOCKED(10);
    2 bytes payload length
    payload

Footer
    1 byte CRC8-MAXIM (with all payload bytes excluding the footer, so 10 to packet.length - 2)
    0x03 (1 byte, constant)

```
Examples:

02fd10000000000001fe02ea1101000100073b03
02fd10000000000001fe02ea1102000100049703
02fd10000000000001fe02ea110200010005c903


02 fd 10 00 00000000 01 fe 02 ea 11 01 00 01 00 07 3b 03 // State 7 = resticted
02 fd 10 00 00000000 01 fe 02 ea 11 02 00 01 00 04 97 03 // Activity 4 = Going Home 
02 fd 10 00 00000000 01 fe 02 ea 11 02 00 01 00 05 c9 03 // Activity 5 = Parked
0  1  2  3  4 5 6 7  8  9  10 11 12 13 14 15 16 17 18 19

0=STX
1=0xFD linked package type
2-3=length remaining = 0x10=16 bytes
4,5,6,7=ChannelId?
8=IsLinked?
9=CRC of 1-8?
10=Packet type (0x00 = request, 0x01 = response, 0x02 = event)
11-12=Major
13-14=Minor (As defined by the protocol, might not be equal to the ordinary protocol)
15-16=Datalenght? Or 16=0 is status, 0=ok?
17=Data?
18=CRC?
19=ETX
```


### Client architecture

The client is built on `asyncio` and uses a non-blocking receive loop for both transports.

**WiFi:** `asyncio.DatagramProtocol.datagram_received` is called for each UDP datagram, which already contains a complete frame.

**UART:** `asyncio.Protocol.data_received` appends bytes to an internal buffer. A frame accumulator uses the embedded length field to detect complete frames and extract them — this is more robust than scanning for the ETX byte, which can appear in payload data.

Once a complete frame is available, both transports call the shared `_dispatch_frame` method, which routes by the marker byte:

| Marker | Meaning | Action |
|--------|---------|--------|
| `0x81` | Extended protocol response | Resolve the `asyncio.Future` registered for that transaction ID |
| `0xfd` | Linked protocol event | Parse and dispatch to all `on_event` callbacks |
| `< 0x80` | Simple protocol response | Resolve the single in-flight simple-command Future |
| other | Heartbeat / unknown | Drop silently |

Each `send_command` call creates a `Future` for its expected response and `await`s it with a configurable timeout. Extended protocol commands can be in-flight concurrently (each keyed by a unique random transaction ID). Simple protocol commands are serialised with an `asyncio.Lock` because the simple protocol has no transaction ID.






### Signals:
A-signal
Signal i begränsingskabeln som skiljer av arbetsområdet för robotgräsklipparen. Kodad information skickas till gräsklipparen via A-signalen. Om det inte finns någon A-signal, t.ex.vid ett avbrott i begränsningsslingan eller strömförsörjningen till laddstationen stannar robotgräsklipparen och visar felmeddelandet
"Ingen slingsignal".

F-signal
Fjärrsignal (ställbar i menyn) från laddningsstationen genererad av en slinga i laddstationens kort. F-signalen används för att låta robotgräsklipparen veta att den är nära laddstationen.
I sällsynta fall kan det vara användbart att minska laddstationens area. Detta kan krävas om laddstationen är t.ex. placerad nära en buske eller en vägg. Detta förhindrar att robotgräsklipparen dockar med laddstationen, även om den kan ta emot signalen från laddstationen.

N-signal
Laddstationens närsignal är cirka 1 meter. Signalen genereras av en slinga i laddstationens kort. N-signalen kommer att leda Automower korrekt in i laddstationen så att laddningskontakterna på klipparen och laddstationen berör varandra.
Automower kan inte komma in i laddstationen utan N-signal. Gräsklipparen stannar och ger felmeddelandet "låg batterispänning".

SK1-SK2-signal
Signalen som laddningsstationen skickar via guidekabeln. Guidesignalen leder klipparen till laddstationen, men den kan också användas för att leda klipparen till ett avlägset område. Guidekabel 1 (SK1) och guidekabel 2 (SK2) är markerade med anslutningarna på baksidan av laddstationen.