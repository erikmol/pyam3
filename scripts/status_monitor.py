#!/usr/bin/env python
"""
status_monitor.py — Static polling monitor for Husqvarna Automower.

Polls basic information about the mower every 30 s.
Logs to a JSONL file only on start and when any value changes.
Also tracks connection health via ping/pong timing.

Transport:
  --port COM3            Physical UART
  --host 192.168.1.100   WiFi via ESPHome TCP bridge (default port 8080)
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from logging_mower import LoggingWifiMower, LoggingUartMower

POLL_COMMANDS = ["IsCharging","GetBatteryLevel", "GetMode", "GetActivity", "GetState", "GetOverride"]
PING_TIMEOUT = 10.0  # seconds before connection considered lost


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_jsonl(fp, obj: dict) -> None:
    fp.write(json.dumps(obj, default=str) + "\n")
    fp.flush()


def _fmt_value(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, dict):
        return ", ".join(f"{k}={val}" for k, val in v.items())
    return str(v)


def _print_status(state: dict, connected: bool, cycle: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    conn = "CONNECTED" if connected else "DISCONNECTED"
    print(f"\n{'─' * 60}")
    print(f"  Cycle {cycle}  {ts}  [{conn}]")
    print(f"{'─' * 60}")
    for cmd in POLL_COMMANDS:
        val = _fmt_value(state.get(cmd))
        print(f"  {cmd:<20} {val}")
    print(f"{'─' * 60}", flush=True)


def _make_mower(args):
    if args.host:
        return LoggingWifiMower(args.host, port=args.tcp_port)
    return LoggingUartMower(args.port, baudrate=args.baud)


def _transport_label(args) -> str:
    if args.host:
        return f"{args.host}:{args.tcp_port} (WiFi)"
    return f"{args.port} @ {args.baud} baud"


def _resolve_log_path(log_dir: Path, append: bool) -> tuple[Path, str]:
    if append:
        existing = sorted(log_dir.glob("status_????????*.jsonl"))
        if existing:
            return existing[-1], "a"
    return log_dir / f"status_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl", "a"


async def run_monitor(args) -> None:
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path, log_mode = _resolve_log_path(log_dir, args.append)
    print(f"Log: {log_path} ({'appending' if args.append and log_path.exists() else 'new'})")
    print(f"Connecting to {_transport_label(args)}…")

    mower = _make_mower(args)

    last_pong_time: list[float] = [asyncio.get_event_loop().time()]

    def on_event(event: dict) -> None:
        name = event.get("name", "")
        if "pong" in name.lower() or "ping" in name.lower():
            last_pong_time[0] = asyncio.get_event_loop().time()

    mower.on_event(on_event)

    await mower.connect()
    print("Connected. Starting poll loop (Ctrl-C to stop)…\n")

    last_state: dict = {}
    cycle = 0
    connected = True

    with open(log_path, log_mode, encoding="utf-8") as log_fp:
        try:
            while True:
                if not mower.is_connected:
                    await asyncio.sleep(1.0)
                    continue

                cycle += 1
                new_state: dict = {}
                poll_ok = True

                for cmd in POLL_COMMANDS:
                    mower.pop_frames()
                    try:
                        result = await asyncio.wait_for(
                            mower.send_command(cmd), timeout=10.0
                        )
                        new_state[cmd] = result
                        last_pong_time[0] = asyncio.get_event_loop().time()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        new_state[cmd] = None
                        poll_ok = False

                # Send KeepAlive each cycle when requested
                if args.keep_alive and poll_ok:
                    await mower.send_command("KeepAlive")
                    mower.pop_frames()

                # Connection health: check last pong freshness
                age = asyncio.get_event_loop().time() - last_pong_time[0]
                now_connected = poll_ok and (age < PING_TIMEOUT or cycle == 1)
                conn_changed = now_connected != connected
                connected = now_connected

                # Detect changes
                changed_keys = {
                    k for k in POLL_COMMANDS
                    if new_state.get(k) != last_state.get(k)
                }
                first_cycle = cycle == 1

                if first_cycle or changed_keys or conn_changed:
                    _print_status(new_state, connected, cycle)
                    entry: dict = {
                        "type": "status",
                        "timestamp": _utcnow(),
                        "cycle": cycle,
                        "connected": connected,
                        "state": new_state,
                    }
                    if not first_cycle:
                        entry["changed"] = list(changed_keys)
                        if conn_changed:
                            entry["connection_changed"] = True
                    _write_jsonl(log_fp, entry)

                last_state = new_state

                # Drain unsolicited frames; log them and update pong timestamp if seen
                for u in mower.pop_unsolicited():
                    marker = u.get("marker", "")
                    if marker in ("0xfd",):
                        last_pong_time[0] = asyncio.get_event_loop().time()
                    _write_jsonl(log_fp, {
                        "type": "unsolicited",
                        "timestamp": u["t"],
                        "marker": marker,
                        "hex": u["hex"],
                    })

                await asyncio.sleep(args.interval)

        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            # flush remaining unsolicited frames
            for u in mower.pop_unsolicited():
                _write_jsonl(log_fp, {
                    "type": "unsolicited",
                    "timestamp": u["t"],
                    "marker": u.get("marker"),
                    "hex": u["hex"],
                })

    await mower.disconnect()
    print(f"Done. Log: {log_path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Husqvarna Automower static status monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  status_monitor.py --host 192.168.1.100\n"
            "  status_monitor.py --port COM3 --interval 60\n"
        ),
    )
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--port", help="UART serial port (e.g. COM3, /dev/ttyUSB0)")
    transport.add_argument("--host", help="ESPHome bridge hostname or IP")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    parser.add_argument("--tcp-port", type=int, default=8080, help="WiFi TCP port (default: 8080)")
    parser.add_argument("--log-dir", default="logs", help="Log output directory (default: logs/)")
    parser.add_argument(
        "--interval", type=float, default=30.0,
        help="Poll interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--append", action="store_true",
        help="Append to the latest existing log file instead of creating a new one",
    )
    parser.add_argument(
        "--keep-alive", action="store_true",
        help="Send KeepAlive to the mower each poll cycle.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(run_monitor(args))


if __name__ == "__main__":
    main()
