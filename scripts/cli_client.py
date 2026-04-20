#!/usr/bin/env python
"""
cli_client.py — Unified CLI for Husqvarna Automower.

Three modes, selected as subcommands:

  interactive  Interactive prompt with tab-complete and history (default).
  monitor      Passive event listener — no control commands sent.
  bench        Automated read-only command sweep with pretty-printed results.

Transport is selected by flag:
  --port COM3            Physical UART (USB-to-serial adapter)
  --host 192.168.1.100   WiFi via ESPHome TCP bridge (default port 8080)

See scripts/README.md for full usage and log-format documentation.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory

from logging_mower import LoggingUartMower, LoggingWifiMower

COMMANDS_PATH = Path(__file__).resolve().parent.parent / "pyam3" / "protocol" / "commands.json"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _resolve_log_path(log_dir: Path, prefix: str, append: bool) -> Path:
    if append:
        existing = sorted(log_dir.glob(f"{prefix}_????????*.jsonl"))
        if existing:
            return existing[-1]
    return log_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"


def _write_jsonl(fp, obj: dict) -> None:
    fp.write(json.dumps(obj, default=str) + "\n")
    fp.flush()


async def _drain_unsolicited(mower, log_fp_ref: list, interval: float = 0.5) -> None:
    try:
        while True:
            await asyncio.sleep(interval)
            if log_fp_ref:
                for u in mower.pop_unsolicited():
                    _write_jsonl(log_fp_ref[0], {
                        "type": "unsolicited",
                        "timestamp": u["t"],
                        "marker": u.get("marker"),
                        "hex": u["hex"],
                    })
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Transport factory
# ---------------------------------------------------------------------------

def _make_mower(args):
    if args.host:
        return LoggingWifiMower(args.host, port=args.tcp_port)
    return LoggingUartMower(args.port, baudrate=args.baud)


def _transport_label(args) -> str:
    if args.host:
        return f"{args.host}:{args.tcp_port} (WiFi)"
    return f"{args.port} @ {args.baud} baud"


# ---------------------------------------------------------------------------
# interactive mode
# ---------------------------------------------------------------------------

def _load_commands() -> dict:
    with open(COMMANDS_PATH) as f:
        return json.load(f)


def _parse_value(value_str: str, type_str: str):
    if type_str == "bool":
        return value_str.strip().lower() in ("1", "true", "yes")
    return int(value_str.strip())


async def run_interactive(args) -> None:
    commands = _load_commands()
    names = sorted(commands.keys())

    completer = WordCompleter(["break"] + names, ignore_case=True, sentence=True)
    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_while_typing=False,
    )

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = _resolve_log_path(log_dir, "cli", args.append)
    print(f"Log: {log_path} ({'appending' if args.append and log_path.exists() else 'new'})")

    mower = _make_mower(args)

    log_fp_ref: list = []

    def on_event(event: dict) -> None:
        name = event.get("name", "unknown")
        data = event.get("data")
        t = _utcnow()
        print(f"\n  [EVENT] {t}  {name}: {data}\n> ", end="", flush=True)
        if log_fp_ref:
            frames = mower.pop_unsolicited()
            _write_jsonl(log_fp_ref[0], {
                "type": "event",
                "timestamp": t,
                "name": name,
                "data": data,
                "frames": frames,
            })

    mower.on_event(on_event)

    await mower.connect()
    print(f"Connected to {_transport_label(args)}. Tab=autocomplete  ↑↓=history  Ctrl-C=exit")
    print("Special: 'break [duration_s]' sends a UART break (default 0.25s)\n")

    with open(log_path, "a", encoding="utf-8") as log_fp:
        log_fp_ref.append(log_fp)
        drain_task = asyncio.create_task(_drain_unsolicited(mower, log_fp_ref))
        try:
            while True:
                try:
                    raw = await session.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting.")
                    break

                tokens = raw.strip().split()
                if not tokens:
                    continue

                cmd_name = tokens[0]

                if cmd_name.lower() == "break":
                    if args.host:
                        print("  'break' is only supported on UART (--port), not WiFi.")
                        continue
                    duration = float(tokens[1]) if len(tokens) > 1 else 0.25
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, mower._transport.serial.send_break, duration)
                    t = _utcnow()
                    print(f"  UART break sent ({duration}s)")
                    _write_jsonl(log_fp, {"type": "break", "timestamp": t, "duration": duration})
                    continue

                if cmd_name not in commands:
                    print(f"  Unknown command '{cmd_name}'. Press Tab for suggestions.")
                    continue

                cmd = commands[cmd_name]
                request_type: dict = cmd.get("requestType") or {}
                inline_args = tokens[1:]
                kwargs: dict = {}
                cancelled = False
                canonical_tokens = [cmd_name]

                for i, (param, ptype) in enumerate(request_type.items()):
                    if i < len(inline_args):
                        raw_val = inline_args[i]
                    else:
                        try:
                            raw_val = await session.prompt_async(f"  {param} ({ptype}): ")
                        except (EOFError, KeyboardInterrupt):
                            print("\n  (cancelled)")
                            cancelled = True
                            break
                    try:
                        kwargs[param] = _parse_value(raw_val, ptype)
                    except ValueError:
                        print(f"  Invalid value '{raw_val}' for {param} ({ptype})")
                        cancelled = True
                        break
                    canonical_tokens.append(raw_val.strip())

                if cancelled:
                    continue

                canonical = " ".join(canonical_tokens)
                if canonical != raw.strip():
                    session.history.append_string(canonical)

                mower.pop_frames()  # clear any stale frames before the command
                t = _utcnow()
                result = None
                error = None
                try:
                    result = await mower.send_command(cmd_name, **kwargs)
                    if isinstance(result, dict):
                        for key, val in result.items():
                            print(f"  {key}: {val}")
                    elif result is not None:
                        print(f"  {result}")
                    else:
                        print("  (no response data)")
                except Exception as e:
                    error = str(e)
                    print(f"  Error: {e}")
                finally:
                    frames = mower.pop_frames()
                    _write_jsonl(log_fp, {
                        "type": "command",
                        "timestamp": t,
                        "name": cmd_name,
                        "kwargs": kwargs,
                        "result": result,
                        "error": error,
                        "frames": frames,
                    })
                    for u in mower.pop_unsolicited():
                        _write_jsonl(log_fp, {
                            "type": "unsolicited",
                            "timestamp": u["t"],
                            "marker": u.get("marker"),
                            "hex": u["hex"],
                        })

        finally:
            drain_task.cancel()
            await asyncio.gather(drain_task, return_exceptions=True)
            for u in mower.pop_unsolicited():
                _write_jsonl(log_fp, {
                    "type": "unsolicited",
                    "timestamp": u["t"],
                    "marker": u.get("marker"),
                    "hex": u["hex"],
                })
            log_fp_ref.clear()

    await mower.disconnect()
    print(f"Log saved: {log_path}")


# ---------------------------------------------------------------------------
# monitor mode
# ---------------------------------------------------------------------------

async def run_monitor(args) -> None:
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = _resolve_log_path(log_dir, "events", args.append)
    print(f"Log: {log_path} ({'appending' if args.append and log_path.exists() else 'new'})")

    mower = _make_mower(args)

    log_fp_ref: list = []

    def on_event(event: dict) -> None:
        name = event.get("name", "unknown")
        data = event.get("data")
        t = _utcnow()
        print(f"  [EVENT] {t}  {name}: {data}")
        if log_fp_ref:
            frames = mower.pop_unsolicited()
            _write_jsonl(log_fp_ref[0], {
                "type": "event",
                "timestamp": t,
                "name": name,
                "data": data,
                "frames": frames,
            })

    mower.on_event(on_event)

    print(f"Connecting to {_transport_label(args)}…")
    await mower.connect()
    print("Connected. Subscribing to events…")

    await mower.send_command("SubscribeMowerAppEvents")
    await mower.send_command("SubscribePowerEvents")
    mower.pop_frames()
    mower.pop_unsolicited()

    print("Subscribed. Waiting for events (Ctrl-C to stop)…\n")

    with open(log_path, "a", encoding="utf-8") as log_fp:
        log_fp_ref.append(log_fp)
        drain_task = asyncio.create_task(_drain_unsolicited(mower, log_fp_ref))
        try:
            if args.duration > 0:
                await asyncio.sleep(args.duration)
            else:
                await asyncio.get_running_loop().create_future()
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            drain_task.cancel()
            await asyncio.gather(drain_task, return_exceptions=True)
            for u in mower.pop_unsolicited():
                _write_jsonl(log_fp, {
                    "type": "unsolicited",
                    "timestamp": u["t"],
                    "marker": u.get("marker"),
                    "hex": u["hex"],
                })
            log_fp_ref.clear()

    await mower.disconnect()
    print(f"Done. Log: {log_path}")


# ---------------------------------------------------------------------------
# bench mode
# ---------------------------------------------------------------------------

BENCH_STATIC_COMMANDS: list[tuple[str, dict]] = [
    # --- Identity ---
    ("GetSerialNumber",            {}),
    ("GetModel",                   {}),
    # --- Battery ---
    ("GetBatteryLevel",            {}),
    ("IsCharging",                 {}),
    ("GetBatteryData",             {}),
    ("GetRemainingChargingTime",   {}),
    # --- State machine ---
    ("GetState",                   {}),
    ("GetActivity",                {}),
    ("GetMode",                    {}),
    ("GetInternalState",           {}),
    ("GetError",                   {}),
    ("GetPowerMode",               {}),
    # --- Sensors ---
    ("GetSensorData",              {}),
    # --- Override / scheduling ---
    ("GetOverride",                {}),
    ("GetRestrictionReason",       {}),
    ("GetNextStartTime",           {}),
    # --- Auth / power ---
    ("IsOperatorLoggedIn",         {}),
    ("IsChargingPowerConnected",   {}),
    ("GetStartupSequenceRequired", {}),
    # --- Statistics ---
    ("GetAllStatistics",           {}),
    # --- Sleep timers ---
    ("GetTimeBeforeSleep",         {}),
    ("GetTimeWakeupInterval",      {}),
    # --- Counts (drive dynamic fetches below) ---
    ("GetNumberOfMessages",        {}),
    ("GetNumberOfTasks",           {}),
]

_BENCH_MAX_MESSAGES = 5
_BENCH_MAX_TASKS = 5


def _fmt_result(result) -> str:
    if result is None:
        return "—"
    if isinstance(result, dict):
        return ", ".join(f"{k}={v}" for k, v in list(result.items())[:4])
    return str(result)


async def _bench_run_command(mower, log_fp, cmd_name: str, kwargs: dict, cmd_delay: float) -> dict:
    if cmd_delay > 0:
        await asyncio.sleep(cmd_delay)
    mower.pop_frames()
    t_start = _utcnow()
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

    _write_jsonl(log_fp, entry)

    for u in mower.pop_unsolicited():
        _write_jsonl(log_fp, {
            "type": "unsolicited",
            "timestamp": u["t"],
            "marker": u.get("marker"),
            "hex": u["hex"],
        })

    return entry


async def _bench_poll_once(mower, log_fp, cmd_delay: float) -> list[dict]:
    results: list[dict] = []

    for u in mower.pop_unsolicited():
        _write_jsonl(log_fp, {
            "type": "unsolicited",
            "timestamp": u["t"],
            "marker": u.get("marker"),
            "hex": u["hex"],
        })

    for cmd_name, kwargs in BENCH_STATIC_COMMANDS:
        entry = await _bench_run_command(mower, log_fp, cmd_name, kwargs, cmd_delay)
        results.append(entry)

    n_messages = next(
        (e["result"] for e in results if e["command"] == "GetNumberOfMessages"), 0
    )
    if n_messages:
        for msg_id in range(min(int(n_messages), _BENCH_MAX_MESSAGES)):
            entry = await _bench_run_command(mower, log_fp, "GetMessage", {"messageId": msg_id}, cmd_delay)
            results.append(entry)

    n_tasks = next(
        (e["result"] for e in results if e["command"] == "GetNumberOfTasks"), 0
    )
    if n_tasks:
        for task_id in range(min(int(n_tasks), _BENCH_MAX_TASKS)):
            entry = await _bench_run_command(mower, log_fp, "GetTask", {"taskId": task_id}, cmd_delay)
            results.append(entry)

    return results


def _bench_print_table(results: list[dict], cycle: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'─' * 64}")
    print(f"  Cycle {cycle}  {ts}")
    print(f"{'─' * 64}")
    for r in results:
        cmd = r["command"]
        marker = "OK" if r["status"] == "ok" else "--"
        val = _fmt_result(r["result"])
        tx = sum(1 for f in r["frames"] if f["direction"] == "tx")
        rx = sum(1 for f in r["frames"] if f["direction"] == "rx")
        print(f"  [{marker}] {cmd:<38} {val:<20}  ({tx}tx/{rx}rx)")
    print(f"{'─' * 64}")


async def run_bench(args) -> None:
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = _resolve_log_path(log_dir, "bench", args.append)
    print(f"Log: {log_path} ({'appending' if args.append and log_path.exists() else 'new'})")

    mower = _make_mower(args)

    log_fp_ref: list = []

    def on_event(event: dict) -> None:
        if log_fp_ref:
            frames = mower.pop_unsolicited()
            _write_jsonl(log_fp_ref[0], {
                "type": "event",
                "timestamp": _utcnow(),
                "name": event.get("name"),
                "data": event.get("data"),
                "frames": frames,
            })
            print(f"  [EVENT] {event.get('name')}: {event.get('data')}")

    mower.on_event(on_event)

    print(f"Connecting to {_transport_label(args)}…")
    await mower.connect()
    print("Connected.")

    await mower.send_command("SubscribeMowerAppEvents")
    await mower.send_command("SubscribePowerEvents")
    mower.pop_frames()

    cycle = 0
    with open(log_path, "a", encoding="utf-8") as log_fp:
        log_fp_ref.append(log_fp)
        try:
            while True:
                cycle += 1
                results = await _bench_poll_once(mower, log_fp, args.cmd_delay)
                _bench_print_table(results, cycle)
                if args.interval <= 0:
                    break
                await asyncio.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            log_fp_ref.clear()

    await mower.disconnect()
    print(f"Done. Log: {log_path}")


# ---------------------------------------------------------------------------
# Argument parsing & dispatch
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Husqvarna Automower client (UART or WiFi)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  cli_client.py --port COM3 interactive\n"
            "  cli_client.py --host 192.168.1.100 interactive\n"
            "  cli_client.py --host mower.local monitor --duration 120\n"
            "  cli_client.py --port /dev/ttyUSB0 bench --interval 10\n"
        ),
    )
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--port", help="UART serial port (e.g. COM3, /dev/ttyUSB0)")
    transport.add_argument("--host", help="ESPHome bridge hostname or IP for WiFi transport")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    parser.add_argument("--tcp-port", type=int, default=8080, help="WiFi bridge TCP port (default: 8080)")
    parser.add_argument("--log-dir", default="logs", help="Log directory (default: logs/)")
    parser.add_argument(
        "--append", action="store_true",
        help="Append to the latest existing log file instead of creating a new one",
    )

    sub = parser.add_subparsers(dest="mode")
    sub.add_parser("interactive", help="Interactive prompt (default)")

    mon = sub.add_parser("monitor", help="Passive event listener")
    mon.add_argument(
        "--duration", type=float, default=0,
        help="Stop after N seconds; 0 = run until Ctrl-C (default: 0)",
    )

    bench = sub.add_parser("bench", help="Automated read-only command sweep")
    bench.add_argument(
        "--interval", type=float, default=0,
        help="Repeat every N seconds; 0 = one-shot (default: 0)",
    )
    bench.add_argument(
        "--cmd-delay", type=float, default=0.25,
        help="Delay between commands in seconds (default: 0.25)",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.mode == "monitor":
        asyncio.run(run_monitor(args))
    elif args.mode == "bench":
        asyncio.run(run_bench(args))
    else:
        asyncio.run(run_interactive(args))


if __name__ == "__main__":
    main()
