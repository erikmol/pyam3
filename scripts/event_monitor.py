#!/usr/bin/env python3
"""
event_monitor.py — Passive event listener for Husqvarna Automower over UART.

Subscribes to all mower events and logs unsolicited pushes (state changes,
activity changes, power-mode changes) to a timestamped JSONL file, together
with the raw frames that carried them.  Useful for observing mower behaviour
on the bench without sending any control commands.

Usage:
    python scripts/event_monitor.py --port COM3
    python scripts/event_monitor.py --port COM3 --duration 120
    python scripts/event_monitor.py --port /dev/ttyUSB0 --log-dir /tmp/mower
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging_mower import LoggingUartMower  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_jsonl(fp, obj: dict) -> None:
    fp.write(json.dumps(obj, default=str) + "\n")
    fp.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Passive event monitor for Husqvarna Automower over UART"
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
        "--duration", type=float, default=0,
        help="Stop after N seconds; 0 = run until Ctrl-C (default: 0)",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    print(f"Log: {log_path}")

    mower = LoggingUartMower(args.port, baudrate=args.baud)

    _log_fp_ref: list = []

    def on_event(event: dict) -> None:
        """
        Called by the base Mower for every linked-protocol event.
        We pop unsolicited frames here to associate raw bytes with the event.
        Note: pop_unsolicited() returns ALL frames accumulated since the last
        pop, which is typically just the one that triggered this callback.
        """
        name = event.get("name", "unknown")
        data = event.get("data")
        t = utcnow()
        print(f"  [EVENT] {t}  {name}: {data}")

        if _log_fp_ref:
            frames = mower.pop_unsolicited()
            write_jsonl(_log_fp_ref[0], {
                "type": "event",
                "timestamp": t,
                "name": name,
                "data": data,
                "frames": frames,
            })

    mower.on_event(on_event)

    print(f"Connecting to {args.port} @ {args.baud} baud…")
    await mower.connect()
    print("Connected. Subscribing to events…")

    await mower.send_command("SubscribeMowerAppEvents")
    await mower.send_command("SubscribePowerEvents")
    mower.pop_frames()       # discard subscription handshake frames
    mower.pop_unsolicited()  # discard any frames captured during handshake

    print("Subscribed. Waiting for events (Ctrl-C to stop)…\n")

    with open(log_path, "a", encoding="utf-8") as log_fp:
        _log_fp_ref.append(log_fp)
        try:
            if args.duration > 0:
                await asyncio.sleep(args.duration)
            else:
                # Park the coroutine until Ctrl-C
                await asyncio.get_running_loop().create_future()
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            # Log any remaining unsolicited frames not yet tied to an event
            leftovers = mower.pop_unsolicited()
            for u in leftovers:
                write_jsonl(log_fp, {
                    "type": "unsolicited",
                    "timestamp": u["t"],
                    "marker": u.get("marker"),
                    "hex": u["hex"],
                })
            _log_fp_ref.clear()

    await mower.disconnect()
    print(f"Done. Log: {log_path}")


if __name__ == "__main__":
    asyncio.run(main())
