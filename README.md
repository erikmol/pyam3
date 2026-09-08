Python library for controlling a Husqvarna Automower over WiFi (ESP32 TCP bridge) or direct USB-UART.
Commands are sent using a shared async client across both transports, with automatic retry on timeout.

## Docs

| Topic | File |
|-------|------|
| ESP32 WiFi bridge setup | [docs/esp32_bridge.md](docs/esp32_bridge.md) |
| Protocol format (simple / extended / linked) | [docs/protocol.md](docs/protocol.md) |
| Home Assistant custom component | [docs/ha_component.md](docs/ha_component.md) |
| CLI tools | [scripts/README.md](scripts/README.md) |

## Installation

```bash
pip install -e .
```

To use the Home Assistant custom component instead, install it via HACS (as
a custom repository) or manually — see
[docs/ha_component.md](docs/ha_component.md). `pyam3` isn't on PyPI, so the
component's `manifest.json` pulls it directly from this repo; HA installs it
automatically, no separate `pip install` needed.

## Quick start

### WiFi (via ESP32 bridge)

```python
import asyncio
from pyam3.client import WifiSerialMower

async def main():
    mower = WifiSerialMower("mower-bridge.local", port=8080)
    await mower.connect()

    print(await mower.battery_level())   # e.g. 85
    print(await mower.serial_number())   # e.g. 12345678

    state = await mower.send_command("GetState")
    mode  = await mower.send_command("GetMode")

    await mower.send_command("SetMode", mode=0)
    await mower.send_command("SetOverrideMow", duration=3600)

    await mower.disconnect()

asyncio.run(main())
```

### USB / UART

```python
import asyncio
from pyam3.uart_client import UartMower

async def main():
    mower = UartMower("/dev/ttyUSB0", baudrate=115200)  # Windows: "COM3"
    await mower.connect()

    print(await mower.battery_level())
    await mower.disconnect()

asyncio.run(main())
```

Both transports share the same `send_command`, `battery_level`, and `serial_number` API.

### Subscribing to push events

```python
mower.on_event(lambda event: print(event["name"], event["data"]))
await mower.send_command("SubscribeMowerAppEvents")
# fires whenever the mower reports a state or activity change
```

## Repo layout

```
pyam3/                    ← installable Python library
  __init__.py
  client.py               ← WiFi (TCP) transport
  uart_client.py          ← UART transport
  protocol/
    commands.json         ← command definitions
    base.py / common.py / simple.py / extended.py / linked.py
custom_components/
  mower_wifi/             ← Home Assistant custom component
esphome/                  ← ESP32 WiFi bridge (ESPHome)
scripts/                  ← CLI tools
docs/                     ← documentation
```
