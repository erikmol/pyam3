# ESP32 WiFi Bridge

The ESP32 bridge is implemented as an **ESPHome custom component** located in
`esphome/`.  It turns any ESP32 into a transparent TCP↔UART bridge between the
Python client (`WifiSerialMower`) and the mower's physical serial interface.

---

## Quick start

```bash
pip install esphome

# Copy and edit secrets
cp esphome/secrets.yaml.example esphome/secrets.yaml
$EDITOR esphome/secrets.yaml

# First flash (USB)
esphome run esphome/mower_bridge.yaml

# Subsequent updates (OTA, once on WiFi)
esphome run esphome/mower_bridge.yaml
```

The bridge is reachable at **`mower-bridge.local:8080`** once connected to WiFi.

To connect the Python client:

```python
from pyam3.client import WifiSerialMower
mower = WifiSerialMower("mower-bridge.local", port=8080)
```

---

## Hardware

| Signal | ESP32 pin | Notes |
|--------|-----------|-------|
| UART TX | GPIO17 | → mower RX |
| UART RX | GPIO16 | ← mower TX |
| GND | GND | common ground required |

Adjust pins in `esphome/mower_bridge.yaml` under the `uart:` section.
Avoid GPIO1/GPIO3 (UART0, used by USB-Serial for flashing).

---

## Architecture

```
[Python host]  ──TCP 8080──►  [ESP32 ESPHome]  ──UART 115200──►  [Mower]
[Python host]  ◄──TCP 8080──  [ESP32 ESPHome]  ◄──UART 115200──  [Mower]
```

The component (`mower_bridge.cpp`) runs inside ESPHome's cooperative `loop()`:

| Direction | Behaviour |
|-----------|-----------|
| TCP → UART | Accumulates a full STX…ETX frame, intercepts PING heartbeat, forwards everything else to UART |
| UART → TCP | Drains the UART RX buffer and forwards raw bytes to the TCP client (Python handles reassembly) |

---

## Bridge-layer heartbeat

The Python client sends a periodic PING to verify the bridge is alive beyond
the TCP keepalive.  The component replies with PONG without touching the UART.

| Frame | Bytes (hex) |
|-------|-------------|
| PING (Python → bridge) | `02 50 49 4E 47 03` |
| PONG (bridge → Python) | `02 50 4F 4E 47 03` |

Python sends a PING every 10 s and expects a PONG within 5 s; a missing PONG
triggers an automatic reconnect.

---

## Component files

```
esphome/
├── mower_bridge.yaml              # ESPHome device configuration
└── components/
    └── mower_bridge/
        ├── __init__.py            # ESPHome codegen schema
        ├── mower_bridge.h         # Component header
        └── mower_bridge.cpp       # TCP server + frame forwarding logic
```

---

## Frame length calculation

The component uses the same logic as the Python `_frame_length()` helper to
determine when a complete mower frame has been received from TCP:

```c
// Extended / Linked (marker 0x81 or 0xFD):  total = remaining_LE + 4
// Simple (any other marker):                 total = payload_len  + 5
int frame_length(const uint8_t *buf, size_t avail) {
    if (avail < 3) return -1;
    uint8_t marker = buf[1];
    if (marker == 0x81 || marker == 0xFD) {
        if (avail < 4) return -1;
        uint16_t remaining = buf[2] | (buf[3] << 8);
        return (int)remaining + 4;
    }
    return (int)buf[2] + 5;
}
```

---

## Configuration reference

Key options in `mower_bridge.yaml`:

| Key | Default | Description |
|-----|---------|-------------|
| `uart_id` | — | ID of the `uart:` block wired to the mower |
| `port` | `8080` | TCP port the bridge listens on |
| `uart.tx_pin` | GPIO17 | UART TX pin |
| `uart.rx_pin` | GPIO16 | UART RX pin |
| `uart.baud_rate` | 115200 | Must match mower baud rate |
| `uart.rx_buffer_size` | 512 | UART RX ring buffer (bytes) |

---

## OTA updates

After the first USB flash, all subsequent firmware updates can be pushed
wirelessly:

```bash
esphome run esphome/mower_bridge.yaml
```

ESPHome will detect that the device is on the network and use OTA automatically.
