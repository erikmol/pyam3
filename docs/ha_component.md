# Home Assistant Custom Component

`custom_components/mower_wifi/` integrates the Husqvarna Automower into Home Assistant via the ESP32 WiFi bridge.

## Setup

### Option A: Install via HACS (recommended)

`pyam3` isn't published on PyPI, so the integration's `manifest.json` pins its
`requirements` entry to this same repo, e.g.:

```json
"requirements": ["pyam3 @ git+https://github.com/erikmol/pyam3.git@v0.1.1"]
```

Home Assistant installs that pinned git ref automatically when the
integration is set up — no separate `pip install` step is needed.

1. In HACS, open the 3-dot menu → **Custom repositories**
2. Add `https://github.com/erikmol/pyam3` as an **Integration**
3. Install **Automower WiFi** from HACS
4. Restart Home Assistant

Then skip to [Add the integration](#add-the-integration) below.

### Option B: Manual install

1. From the repo root, install the library:

   ```bash
   pip install -e .
   ```

2. Copy `custom_components/mower_wifi/` into your HA config directory:

   ```
   config/
     custom_components/
       mower_wifi/
   ```

### Add the integration

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

The `lawn_mower` entity maps the combination of `state`, `activity`, `mode`, `override`, and `error_code` to `LawnMowerActivity`. Priority is top-down — first match wins.

### error

| State | Activity | Mode | Override | error_code | Condition |
|-------|----------|------|----------|------------|-----------|
| FATAL_ERROR | any | any | any | any | Hardware fault |
| ERROR | any | any | any | any | Recoverable error, check error_code |
| WAIT_FOR_SAFETYPIN | any | any | any | any | Safety pin missing |
| any | any | any | any | ≠ 0 | Active error code |

### mowing

| State | Activity | Mode | Override | Semantic meaning |
|-------|----------|------|----------|------------------|
| IN_OPERATION | GOING_OUT | any | any | Leaving dock, about to mow |
| IN_OPERATION | MOWING | AUTO / DEMO | NONE | Scheduled mow (DEMO: blades off) |
| IN_OPERATION | MOWING | any | FORCEDMOW | Force-mow override active |

### returning

| State | Activity | Mode | Override | Semantic meaning |
|-------|----------|------|----------|------------------|
| IN_OPERATION | GOING_HOME | any | any | Returning to dock |

### paused

| State | Activity | Mode | Override | Semantic meaning |
|-------|----------|------|----------|------------------|
| PAUSED | any | any | any | User-initiated pause |
| STOPPED | any | any | any | Stopped, requires manual action |
| IN_OPERATION | STOPPED_IN_GARDEN | any | any | Stuck in garden, needs manual help |

### docked

All of these are `docked` in HA. They are semantically distinct but HA has no sub-states for them.

| State | Activity | Mode | Override | Semantic meaning |
|-------|----------|------|----------|------------------|
| IN_OPERATION | CHARGING | any | any | Charging after mow (low battery) |
| RESTRICTED | PARKED | AUTO | NONE | Waiting for next scheduled start |
| RESTRICTED | PARKED | AUTO | FORCEDPARK | Force-parked until next schedule |
| RESTRICTED | PARKED | HOME | any | Parked forever — no schedule in use |
| PENDING_START | any | any | any | Imminent departure (warming up) |
| OFF | any | any | any | Mower is off |

> To distinguish "waiting for schedule" from "force-parked", read `data.override` (0 = NONE, 1 = FORCEDPARK).  
> To detect "parked forever" (HOME mode), read `data.mode` (2 = HOME).

### Protocol enums

**MowerState (GetState, uint8)**

| Value | Name | Notes |
|-------|------|-------|
| 0 | OFF | |
| 1 | WAIT_FOR_SAFETYPIN | → `error` |
| 2 | STOPPED | requires manual action → `paused` |
| 3 | FATAL_ERROR | → `error` |
| 4 | PENDING_START | about to depart → `docked` |
| 5 | PAUSED | user-paused → `paused` |
| 6 | IN_OPERATION | see activity |
| 7 | RESTRICTED | calendar or override park → `docked` |
| 8 | ERROR | check error_code → `error` |

**MowerActivity (GetActivity, uint8)**

| Value | Name | Notes |
|-------|------|-------|
| 0 | NONE | |
| 1 | CHARGING | → `docked` |
| 2 | GOING_OUT | → `mowing` |
| 3 | MOWING | → `mowing` |
| 4 | GOING_HOME | → `returning` |
| 5 | PARKED | → `docked` |
| 6 | STOPPED_IN_GARDEN | needs manual help → `paused` |

**ModeOfOperation (GetMode, uint8)**

| Value | Name | Notes |
|-------|------|-------|
| 0 | AUTO | Normal scheduled operation |
| 1 | MANUAL | Manual mode |
| 2 | HOME | Parked forever, ignores schedule |
| 3 | DEMO | Mows without blade operation |
| 4 | POI | |

**OverrideAction (GetOverride, uint8)**

| Value | Name | Notes |
|-------|------|-------|
| 0 | NONE | No active override |
| 1 | FORCEDPARK | Parked until next scheduled start |
| 2 | FORCEDMOW | Mowing outside of schedule |

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
