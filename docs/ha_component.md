# Home Assistant Custom Component

`custom_components/mower_wifi/` integrates the Husqvarna Automower into Home Assistant via the ESP32 WiFi bridge.

## Setup

### 1. Install the library

From the repo root:

```bash
pip install -e .
```

Or add to HA's `requirements.txt` / HACS config using a git URL.

### 2. Copy the component

Copy `custom_components/mower_wifi/` into your HA config directory:

```
config/
  custom_components/
    mower_wifi/
```

### 3. Add the integration

1. Restart Home Assistant
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **Automower WiFi**
4. Enter the bridge hostname (e.g. `mower-bridge.local`) and port (default `8080`)
5. HA validates the connection and creates the device

The bridge must be set up first — see [esp32_bridge.md](esp32_bridge.md).

---

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| Mower | `lawn_mower` | Main control entity (start / dock / pause) |
| Battery | `sensor` | Battery level (%) |
| Remaining charge time | `sensor` | Minutes until fully charged (available only while charging) |
| Activity | `sensor` | Raw activity code (diagnostic) |
| State | `sensor` | Raw state code (diagnostic) |
| Mode | `sensor` | Raw mode code (diagnostic) |
| Charging | `binary_sensor` | Whether the mower is currently charging |

---

## State mapping

The `lawn_mower` entity maps raw mower codes to `LawnMowerActivity`:

| Condition | HA state |
|-----------|----------|
| `error_code != 0` or state is FATAL_ERROR / ERROR | `error` |
| activity is GOING_OUT or MOWING | `mowing` |
| activity is GOING_HOME | `returning` |
| state is PAUSED or activity is STOPPED_IN_GARDEN | `paused` |
| everything else | `docked` |

### MowerState codes (uint8)

| Value | Name |
|-------|------|
| 0 | OFF |
| 1 | WAIT_FOR_SAFETYPIN |
| 2 | STOPPED |
| 3 | FATAL_ERROR |
| 4 | PENDING_START |
| 5 | PAUSED |
| 6 | IN_OPERATION |
| 7 | RESTRICTED |
| 8 | ERROR |

### MowerActivity codes (uint8)

| Value | Name |
|-------|------|
| 0 | NONE |
| 1 | CHARGING |
| 2 | GOING_OUT |
| 3 | MOWING |
| 4 | GOING_HOME |
| 5 | PARKED |
| 6 | STOPPED_IN_GARDEN |

---

## Commands

| HA action | Commands sent |
|-----------|---------------|
| Start mowing | `SetMode(mode=AUTO)` → `SetOverrideMow(duration=14400)` → `StartTrigger` |
| Dock | `SetOverrideParkUntilNextStart` → `StartTrigger` |
| Pause | `Pause` |

`StartTrigger` is expected to return a non-OK response — this is handled gracefully.

---

## Data flow

```
WifiSerialMower  (TCP → ESP32 → UART → Mower)
      │
      │  poll every 30 s + push StateEvent / ActivityEvent
      ▼
MowerCoordinator
      │
      ├── MowerLawnMower (lawn_mower entity)
      ├── MowerSensor    (battery, charge time, activity, state, mode)
      └── MowerBinarySensor (is_charging)
```
