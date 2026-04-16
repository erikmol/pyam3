# ESP32 WiFi Bridge — Implementation Requirements

The ESP32 acts as a **transparent TCP↔UART bridge** between the Python client
(`WifiSerialMower`) and the mower's physical UART interface.  It does **not**
parse or interpret mower protocol frames — it forwards bytes in both directions,
with one exception: the bridge-layer heartbeat (PING/PONG) is handled locally
and must not be forwarded to the mower.

---

## Architecture

```
[Python host]  ──TCP 8080──►  [ESP32]  ──UART 115200──►  [Mower]
[Python host]  ◄──TCP 8080──  [ESP32]  ◄──UART 115200──  [Mower]
```

---

## TCP Server

| Parameter | Value |
|-----------|-------|
| Role | TCP server (ESP32 listens, Python connects) |
| Port | **8080** (configurable) |
| Clients | One at a time; close and re-`accept()` on disconnect |
| Keepalive | Enable `SO_KEEPALIVE` on the accepted socket |
| Addressing | Use a static IP or advertise via mDNS as `mower-bridge.local` |

**Accept loop behaviour:**

1. Listen on port 8080.
2. Accept one client connection.
3. Forward data bidirectionally until the client disconnects or an error occurs.
4. Close the socket and return to step 1 immediately — do not delay.

---

## UART Interface

| Parameter | Value |
|-----------|-------|
| Baud rate | 115 200 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Flow control | None |
| Pins | UART1 — configure TX/RX explicitly (avoid UART0, used by USB-Serial) |

---

## Frame Forwarding

The mower protocol uses STX (`0x02`) / ETX (`0x03`) framing with an embedded
length field.  The ESP32 **must** accumulate a complete frame before forwarding
it, for two reasons:

1. To identify and discard heartbeat frames before they reach the mower.
2. To avoid sending partial frames over UART after a TCP reconnect.

### Frame length calculation

```c
int frame_length(const uint8_t *buf, int available) {
    if (available < 3) return -1;          // need more bytes
    uint8_t marker = buf[1];
    if (marker == 0x81 || marker == 0xFD) {
        if (available < 4) return -1;
        uint16_t remaining = buf[2] | (buf[3] << 8);
        return (int)remaining + 4;         // Extended / Linked
    }
    return (int)buf[2] + 5;               // Simple protocol
}
```

### Forwarding rules

| Direction | Action |
|-----------|--------|
| TCP → UART | Accumulate a full frame. If it is a PING heartbeat, reply with PONG over TCP and discard. Otherwise write the frame to UART. |
| UART → TCP | Accumulate a full frame. Forward it as-is over TCP. |

---

## Bridge-Layer Heartbeat

The Python client sends a periodic PING to verify the bridge is alive (not
just that the TCP socket is open).  The bridge must respond with PONG without
forwarding either frame to the mower.

| Frame | Bytes (hex) |
|-------|-------------|
| PING (client → bridge) | `02 50 49 4E 47 03` |
| PONG (bridge → client) | `02 50 4F 4E 47 03` |

Detection: if `buf[0] == 0x02 && buf[1] == 0x50`, it is a heartbeat frame.
Length is always 6 bytes.

The Python client sends a PING every **10 s** and waits up to **5 s** for a
PONG.  A missing PONG triggers a reconnect on the Python side, so the bridge
does not need its own reconnect logic for the TCP side — just re-`accept()`.

---

## Watchdog

If no bytes are received from the mower UART for **30 s** while a TCP client
is connected, reset the UART peripheral (re-initialise baud rate and pins).
This recovers from UART hangs without requiring a full ESP32 reboot.

A hardware watchdog timer should also be enabled with a **60 s** timeout to
recover from firmware lockups.

---

## Network Configuration

| Option | Recommendation |
|--------|---------------|
| WiFi mode | Station (STA) — connect to home network |
| IP assignment | Static IP preferred; DHCP with reserved lease is acceptable |
| mDNS hostname | `mower-bridge` → resolves as `mower-bridge.local` |
| DNS-SD service | Advertise `_mower._tcp` on port 8080 for future auto-discovery |

Do **not** use SoftAP mode in production — it creates an open access point
that anyone nearby could connect to.

---

## Build Requirements

| Component | Minimum |
|-----------|---------|
| SDK | ESP-IDF ≥ 5.1 or Arduino ESP32 core ≥ 3.0 |
| Chip | ESP32, ESP32-S3, or ESP32-C3 (any with WiFi + UART1) |
| Flash | 4 MB (OTA requires 2 × app partitions) |
| RAM | 256 KB DRAM sufficient |
| OTA | Recommended — enables firmware updates without physical access |

Relevant ESP-IDF components: `esp_wifi`, `esp_netif`, `mdns`, `lwip` (sockets),
`driver/uart`.

---

## Implementation Checklist

- [ ] WiFi station connect with auto-reconnect
- [ ] mDNS hostname advertisement
- [ ] TCP server accept loop (single client)
- [ ] `SO_KEEPALIVE` on accepted socket
- [ ] Frame accumulator on TCP RX path
- [ ] Heartbeat PING detection and PONG reply
- [ ] Frame forwarding TCP → UART (excluding heartbeats)
- [ ] Frame accumulator on UART RX path
- [ ] Frame forwarding UART → TCP
- [ ] UART watchdog (30 s no-data reset)
- [ ] Hardware watchdog (60 s)
- [ ] OTA update partition layout
