# scripts/

`cli_client.py` is the single entry point for all interaction with a Husqvarna
Automower.  It supports two transports and three operating modes.

## Transport

Exactly one transport flag is required:

| Flag | Transport | Description |
|---|---|---|
| `--port COM3` | UART | Direct USB-to-serial connection |
| `--host 192.168.1.100` | WiFi | ESPHome TCP bridge on the LAN |

```
# Physical UART
python scripts/cli_client.py --port COM3 interactive

# WiFi via ESPHome bridge
python scripts/cli_client.py --host mower.local interactive
python scripts/cli_client.py --host 192.168.1.100 --tcp-port 8080 bench
```

### Transport options

| Option | Default | Applies to |
|---|---|---|
| `--port PORT` | — | UART — serial port, e.g. `COM3` or `/dev/ttyUSB0` |
| `--baud N` | `115200` | UART — baud rate |
| `--host HOST` | — | WiFi — bridge hostname or IP |
| `--tcp-port N` | `8080` | WiFi — TCP port on the bridge |

The WiFi transport connects to an ESP32 running the `mower_bridge` ESPHome
component (`esphome/`).  It handles automatic reconnection with exponential
backoff and a PING/PONG keepalive every 10 s.

## Modes

### `interactive` (default)

Interactive prompt with tab-completion and in-session history.

```
python scripts/cli_client.py --port COM3 interactive
python scripts/cli_client.py --port COM3          # interactive is the default
python scripts/cli_client.py --host mower.local
```

- Tab-complete any command name from `protocol/commands.json`.
- If a command takes parameters and you omit them, the prompt asks for each one.
- `break [duration_s]` — sends a UART break signal (UART transport only, default 0.25 s).
- Log file: `logs/cli_YYYYMMDD_HHMMSS.jsonl`

### `monitor`

Passive listener.  Subscribes to `SubscribeMowerAppEvents` and
`SubscribePowerEvents` at startup, then sits quietly and logs every unsolicited
push.  No control commands are ever sent.

```
python scripts/cli_client.py --port COM3 monitor
python scripts/cli_client.py --host mower.local monitor --duration 120
```

| Option | Default | Description |
|---|---|---|
| `--duration N` | `0` | Stop after N seconds; `0` = run until Ctrl-C |

Log file: `logs/events_YYYYMMDD_HHMMSS.jsonl`

### `bench`

Automated read-only sweep of all safe commands.  Runs the full command suite
once (or repeatedly) and prints a pretty-printed ASCII table after each cycle.
Useful for bench validation and building response-emulator databases.

```
python scripts/cli_client.py --port COM3 bench
python scripts/cli_client.py --host mower.local bench --interval 10 --cmd-delay 0.1
```

| Option | Default | Description |
|---|---|---|
| `--interval N` | `0` | Repeat every N seconds; `0` = one-shot |
| `--cmd-delay N` | `0.25` | Delay between commands (seconds) |

Also subscribes to events so any unsolicited pushes during the sweep are
captured in the log.  Log file: `logs/bench_YYYYMMDD_HHMMSS.jsonl`

## Common options

| Option | Default | Description |
|---|---|---|
| `--log-dir DIR` | `logs/` | Directory for JSONL log files |

## JSONL log format

Every mode writes newline-delimited JSON.  Each line is one of:

### `command`
```json
{
  "type": "command",
  "timestamp": "2025-01-15T10:23:45.123+00:00",
  "name": "GetBatteryLevel",
  "kwargs": {},
  "result": 87,
  "error": null,
  "frames": [
    {"direction": "tx", "hex": "...", "t": "..."},
    {"direction": "rx", "hex": "...", "t": "..."}
  ]
}
```

In `bench` mode the key is `"command"` (not `"name"`) and an extra `"status"`
field (`"ok"` / `"no_response"`) is included.

### `event`
```json
{
  "type": "event",
  "timestamp": "2025-01-15T10:23:46.000+00:00",
  "name": "MowerAppStatusEvent",
  "data": {"state": 2, "activity": 6},
  "frames": [...]
}
```

### `unsolicited`
Raw frames that arrived outside a request/response exchange.
```json
{
  "type": "unsolicited",
  "timestamp": "2025-01-15T10:23:46.001+00:00",
  "marker": "some_marker",
  "hex": "deadbeef..."
}
```

### `break` (`interactive` + UART only)
```json
{
  "type": "break",
  "timestamp": "2025-01-15T10:24:00.000+00:00",
  "duration": 0.25
}
```
