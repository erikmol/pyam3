# Protocol Documentation

There are three protocol layers used to communicate with the mower.

## References

1. **Simple protocol** — documented in [wall-e-esp8266-automower](https://gitlab.com/nbrgmn/wall-e-esp8266-automower/-/tree/feature/RestructureProject) and a [mikrocontroller.net thread](https://www.mikrocontroller.net/topic/304526?page=single#4403150)
2. **Extended protocol** — structure sourced from [Husqvarna Research hrp](https://github.com/HusqvarnaResearch/hrp)
3. **Linked protocol** — studied in [AutoMower-BLE](https://github.com/alistair23/AutoMower-BLE)

---

## Command definitions

Commands are defined in `pyam3/protocol/commands.json` as a map of name → descriptor:

```json
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

---

## Simple and extended protocol

### Request format

1. `0x02` — STX marker
2. Protocol header:
   - **Extended** (msgType ≥ 0x7F): 6 bytes — `0x81` marker, 2-byte remaining length (LE), 1-byte transaction ID, 2-byte message type (high bit set, big-endian)
   - **Simple** (msgType < 0x7F): 1 byte — message type
3. 1 byte — payload length
4. N bytes — payload (includes sub command)
5. 1 byte — CRC8-Maxim checksum
6. `0x03` — ETX marker

#### Simple example

`021401014e03` — GetBatteryData (type 20, sub 1):

```
| 02  | 14      | 01  | 01  | 4e  | 03  |
| STX | MSGTYPE | LEN | PLD | CRC | ETX |
```

#### Extended example

`0281070042900a0114d103` — GetBatteryLevel (type 4106, sub 20), transaction ID `0x42`:

```
| 02  | 81  | 07 00  | 42   | 90 0a   | 01  | 14  | d1  | 03  |
| STX | PEM | NBYTES | T ID | MSGTYPE | LEN | PLD | CRC | ETX |
```

### Response format

Same structure as request, but:
- Response message type is one higher than request type
- A status byte (0 = OK) precedes the data
- Sub command is not returned (for extended protocol, transaction ID is used instead)

#### Simple response

`02150c0000005000eaffef03001101cc03`:

```
| 02  | 15      | 0c  | 00     | 00 00 50 00 ea ff ef 03 00 11 01 | cc  | 03  |
| STX | MSGTYPE | LEN | STATUS | DATA                             | CRC | ETX |
```

#### Extended response

`02810800039239020000da03`:

```
| 02  | 81  | 08 00  | 03   | 92 39   | 02  | 00     | 00   | da  | 03  |
| STX | PEM | NBYTES | T ID | MSGTYPE | LEN | STATUS | DATA | CRC | ETX |
```

---

## Linked protocol

Used for unsolicited push events (state changes, activity changes).

### Header (9 bytes)

| Offset | Size | Description |
|--------|------|-------------|
| 0 | 1 | `0x02` STX |
| 1 | 1 | `0xFD` linked-packet marker |
| 2–3 | 2 | Remaining length (packet length − 4) |
| 4–7 | 4 | Channel ID (can be 0) |
| 8 | 1 | `is_linked` flag |
| 9 | 1 | CRC8-Maxim of bytes 1–8 |

### Payload

**`is_linked == 0x00`:** 1-byte message type + variable payload

**`is_linked == 0x01`:**

| Field | Size | Notes |
|-------|------|-------|
| Packet type | 1 | `0x00` request, `0x01` response, `0x02` event |
| `0xAF` | 1 | Constant (request/response only) |
| Message type | 2 | |
| Sub command | 2 | |
| Result | 1 | Response only. 0=OK, 1=UNKNOWN_ERROR, 2=INVALID_VALUE … |
| Payload length | 2 | |
| Payload | N | |

### Footer

| Field | Size |
|-------|------|
| CRC8-Maxim (bytes 10 to end−2) | 1 |
| `0x03` ETX | 1 |

### Examples

```
02 fd 10 00 00000000 01 fe 02 ea 11 01 00 01 00 07 3b 03  // State 7 = Restricted
02 fd 10 00 00000000 01 fe 02 ea 11 02 00 01 00 04 97 03  // Activity 4 = Going Home
02 fd 10 00 00000000 01 fe 02 ea 11 02 00 01 00 05 c9 03  // Activity 5 = Parked
```

---

## Client architecture

The client is built on `asyncio` with a non-blocking receive loop for both transports.

- **WiFi**: `asyncio.StreamReader` (TCP) feeds raw bytes into `_FrameAccumulator`
- **UART**: `asyncio.Protocol.data_received` feeds raw bytes into `_FrameAccumulator`

The accumulator uses the embedded length field to extract complete frames — more robust than scanning for ETX, which can appear in payload data.

Once a complete frame is available, `_dispatch_frame` routes by marker byte:

| Marker | Meaning | Action |
|--------|---------|--------|
| `0x81` | Extended response | Resolve the `asyncio.Future` for that transaction ID |
| `0xFD` | Linked event | Parse and dispatch to all `on_event` callbacks |
| `< 0x80` | Simple response | Resolve the single in-flight simple-command Future |
| other | Heartbeat / unknown | Drop silently |

Extended protocol commands can be in-flight concurrently (each keyed by a unique random transaction ID). Simple protocol commands are serialised with an `asyncio.Lock` because the simple protocol has no transaction ID.
