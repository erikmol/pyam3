#!/usr/bin/env python
"""
bench_test.py — Workbench function-test script for Husqvarna Automower over UART.

Connects to the mower via USB-to-UART, exercises all safe read-only commands,
and writes a timestamped JSONL log containing parsed results and raw frames.
The raw frames can be used later for debugging or to build a response emulator.

Usage:
    python scripts/bench_test.py --port COM3
    python scripts/bench_test.py --port /dev/ttyUSB0 --baud 115200 --interval 10
    python scripts/bench_test.py --port COM3 --log-dir /tmp/mower-logs
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging_mower import LoggingUartMower  # noqa: E402  (scripts/ is on sys.path)


# ---------------------------------------------------------------------------
# Command suite — all read-only, safe on a workbench mower.
# Each entry: (command_name, request_kwargs)
# ---------------------------------------------------------------------------

STATIC_COMMANDS: list[tuple[str, dict]] = [
    # --- Identity ---
    ("GetSerialNumber",          {}),
    ("GetModel",                 {}),
    # --- Battery ---
    ("GetBatteryLevel",          {}),
    ("IsCharging",               {}),
    ("GetBatteryData",           {}),
    ("GetRemainingChargingTime", {}),
    # --- State machine ---
    ("GetState",                 {}),
    ("GetActivity",              {}),
    ("GetMode",                  {}),
    ("GetInternalState",         {}),
    ("GetError",                 {}),
    ("GetPowerMode",             {}),
    # --- Sensors ---
    ("GetSensorData",            {}),
    # --- Override / scheduling ---
    ("GetOverride",              {}),
    ("GetRestrictionReason",     {}),
    ("GetNextStartTime",         {}),
    # --- Auth / power ---
    ("IsOperatorLoggedIn",       {}),
    ("IsChargingPowerConnected", {}),
    ("GetStartupSequenceRequired", {}),
    # --- Statistics ---
    ("GetAllStatistics",         {}),
    # --- Sleep timers ---
    ("GetTimeBeforeSleep",       {}),
    ("GetTimeWakeupInterval",    {}),
    # --- Counts (used to drive dynamic fetches below) ---
    ("GetNumberOfMessages",      {}),
    ("GetNumberOfTasks",         {}),
]

# How many individual messages / tasks to fetch per cycle
MAX_MESSAGES = 5
MAX_TASKS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_jsonl(fp, obj: dict) -> None:
    fp.write(json.dumps(obj, default=str) + "\n")
    fp.flush()


def fmt_result(result) -> str:
    if result is None:
        return "—"
    if isinstance(result, dict):
        return ", ".join(f"{k}={v}" for k, v in list(result.items())[:4])
    return str(result)


# ---------------------------------------------------------------------------
# Poll logic
# ---------------------------------------------------------------------------

async def _run_command(
    mower: LoggingUartMower,
    log_fp,
    cmd_name: str,
    kwargs: dict,
    cmd_delay: float = 0.0,
) -> dict:
    """Send one command, capture frames, write JSONL, return result dict."""
    if cmd_delay > 0:
        await asyncio.sleep(cmd_delay)
    mower.pop_frames()          # discard any leftover frames from previous command
    t_start = utcnow()
    result = await mower.send_command(cmd_name, **kwargs)
    frames = mower.pop_frames()

    entry: dict = {
        "type": "command",
        "timestamp": t_start,
        "command": cmd_name,
        "status": "ok" if result is not None else "no_response",
        "result": result,
        "frames": frames,
    }
    if kwargs:
        entry["params"] = kwargs

    write_jsonl(log_fp, entry)

    for u in mower.pop_unsolicited():
        write_jsonl(log_fp, {
            "type": "unsolicited",
            "timestamp": u["t"],
            "marker": u.get("marker"),
            "hex": u["hex"],
        })

    return entry


async def poll_once(mower: LoggingUartMower, log_fp, cmd_delay: float = 0.0) -> list[dict]:
    """Run one complete poll cycle; flush unsolicited frames first."""
    results: list[dict] = []

    # Flush any unsolicited frames that arrived between poll cycles
    for u in mower.pop_unsolicited():
        write_jsonl(log_fp, {
            "type": "unsolicited",
            "timestamp": u["t"],
            "marker": u.get("marker"),
            "hex": u["hex"],
        })

    # Static read-only commands
    for cmd_name, kwargs in STATIC_COMMANDS:
        entry = await _run_command(mower, log_fp, cmd_name, kwargs, cmd_delay)
        results.append(entry)

    # Dynamic: fetch individual messages
    n_messages = next(
        (e["result"] for e in results if e["command"] == "GetNumberOfMessages"), 0
    )
    if n_messages:
        for msg_id in range(min(int(n_messages), MAX_MESSAGES)):
            entry = await _run_command(mower, log_fp, "GetMessage", {"messageId": msg_id}, cmd_delay)
            results.append(entry)

    # Dynamic: fetch individual tasks
    n_tasks = next(
        (e["result"] for e in results if e["command"] == "GetNumberOfTasks"), 0
    )
    if n_tasks:
        for task_id in range(min(int(n_tasks), MAX_TASKS)):
            entry = await _run_command(mower, log_fp, "GetTask", {"taskId": task_id}, cmd_delay)
            results.append(entry)

    return results


def print_table(results: list[dict], cycle: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'─' * 64}")
    print(f"  Cycle {cycle}  {ts}")
    print(f"{'─' * 64}")
    for r in results:
        cmd = r["command"]
        ok = r["status"] == "ok"
        marker = "OK" if ok else "--"
        val = fmt_result(r["result"])
        tx = sum(1 for f in r["frames"] if f["direction"] == "tx")
        rx = sum(1 for f in r["frames"] if f["direction"] == "rx")
        print(f"  [{marker}] {cmd:<38} {val:<20}  ({tx}tx/{rx}rx)")
    print(f"{'─' * 64}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bench function-test for Husqvarna Automower over UART"
    )
    parser.add_argument(
        "--port", required=True,
        help="Serial port (e.g. COM3, /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud", type=int, default=115200,
        help="Baud rate (default: 115200)",
    )
    parser.add_argument(
        "--log-dir", default="logs",
        help="Directory for JSONL log files (default: logs/)",
    )
    parser.add_argument(
        "--interval", type=float, default=0,
        help="Repeat every N seconds; 0 = one-shot (default: 0)",
    )
    parser.add_argument(
        "--cmd-delay", type=float, default=0.25,
        help="Delay in seconds between commands (default: 0.25)",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    print(f"Log: {log_path}")

    mower = LoggingUartMower(args.port, baudrate=args.baud)

    # Event callback — parsed linked-protocol events also land in the log
    _log_fp_ref: list = []

    def on_event(event: dict) -> None:
        if _log_fp_ref:
            frames = mower.pop_unsolicited()
            write_jsonl(_log_fp_ref[0], {
                "type": "event",
                "timestamp": utcnow(),
                "name": event.get("name"),
                "data": event.get("data"),
                "frames": frames,
            })
            print(f"  [EVENT] {event.get('name')}: {event.get('data')}")

    mower.on_event(on_event)

    print(f"Connecting to {args.port} @ {args.baud} baud…")
    await mower.connect()
    print("Connected.")

    # Subscribe to unsolicited mower events
    await mower.send_command("SubscribeMowerAppEvents")
    await mower.send_command("SubscribePowerEvents")
    mower.pop_frames()  # discard subscription handshake frames

    cycle = 0
    with open(log_path, "a", encoding="utf-8") as log_fp:
        _log_fp_ref.append(log_fp)
        try:
            while True:
                cycle += 1
                results = await poll_once(mower, log_fp, args.cmd_delay)
                print_table(results, cycle)
                if args.interval <= 0:
                    break
                await asyncio.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            _log_fp_ref.clear()

    await mower.disconnect()
    print(f"Done. Log: {log_path}")


if __name__ == "__main__":
    asyncio.run(main())
