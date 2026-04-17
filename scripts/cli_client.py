import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from logging_mower import LoggingUartMower

COMMANDS_PATH = Path(__file__).resolve().parent.parent / "protocol" / "commands.json"


def _load_commands() -> dict:
    with open(COMMANDS_PATH) as f:
        return json.load(f)


def _parse_value(value_str: str, type_str: str):
    if type_str == "bool":
        return value_str.strip().lower() in ("1", "true", "yes")
    return int(value_str.strip())


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


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_jsonl(fp, obj: dict) -> None:
    fp.write(json.dumps(obj, default=str) + "\n")
    fp.flush()


async def run(port: str, baud: int, log_dir: Path) -> None:
    commands = _load_commands()
    names = sorted(commands.keys())

    completer = WordCompleter(["break"] + names, ignore_case=True, sentence=True)
    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        complete_while_typing=False,
    )

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"cli_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    print(f"Log: {log_path}")

    mower = LoggingUartMower(port, baudrate=baud)

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
    print(f"Connected to {port}. Tab=autocomplete  ↑↓=history  Ctrl-C=exit")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive mower CLI client")
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM3)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--log-dir", default="logs", help="Directory for JSONL log files (default: logs/)")
    args = parser.parse_args()
    asyncio.run(run(args.port, args.baud, Path(args.log_dir)))


if __name__ == "__main__":
    main()
