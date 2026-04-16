"""
logging_mower.py — Frame-capturing subclass of UartMower.

Intercepts every raw TX and RX frame before protocol dispatch so that
all traffic — including unsolicited heartbeats and events that the base
class would silently drop — is recorded for debugging or emulation.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone

from uart_client import UartMower


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LoggingUartMower(UartMower):
    """
    UartMower that captures every raw TX/RX frame.

    Two lists are maintained:
    - _frame_buffer  : all frames since the last pop_frames() call.
                       Cleared explicitly around each send_command() call
                       so bench_test.py can pair TX+RX per command.
    - _unsolicited_log : frames that the base _dispatch_frame() would drop
                         (unknown TXID, no pending simple future, linked
                         events, unknown markers).  These are heartbeats,
                         proactive pushes, or any traffic not tied to a
                         request we sent.
    """

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        super().__init__(port, baudrate)
        self._frame_buffer: list[dict] = []
        self._unsolicited_log: list[dict] = []

    # ── Transport overrides ───────────────────────────────────────────────────

    def _send_bytes(self, data: bytearray) -> None:
        self._frame_buffer.append(
            {"direction": "tx", "t": _utcnow(), "hex": data.hex()}
        )
        super()._send_bytes(data)

    def _dispatch_frame(self, frame: bytearray) -> None:
        t = _utcnow()
        marker = frame[1] if len(frame) >= 2 else None
        entry = {"direction": "rx", "t": t, "hex": frame.hex()}
        self._frame_buffer.append(entry)

        # Classify frame as unsolicited if no pending future will claim it.
        # We check BEFORE calling super() so the _pending dict still contains
        # the matching future (if any) at this point.
        is_unsolicited = False
        if marker == 0xFD:
            # Linked-protocol events are always unsolicited pushes.
            is_unsolicited = True
        elif marker == 0x81 and len(frame) >= 5:
            tid = frame[4]
            if tid not in self._pending:
                is_unsolicited = True
        elif marker is not None and marker < 0x80:
            if self._simple_future is None:
                is_unsolicited = True
        elif marker is not None and marker not in (0x81, 0xFD):
            # Unknown marker — not handled by the base class.
            is_unsolicited = True

        if is_unsolicited:
            self._unsolicited_log.append(
                {**entry, "marker": f"0x{marker:02x}" if marker is not None else None}
            )

        super()._dispatch_frame(frame)

    # ── Buffer accessors ──────────────────────────────────────────────────────

    def pop_frames(self) -> list[dict]:
        """Return and clear the frame buffer (TX + RX since last call)."""
        frames = self._frame_buffer.copy()
        self._frame_buffer.clear()
        return frames

    def pop_unsolicited(self) -> list[dict]:
        """Return and clear all accumulated unsolicited frames."""
        items = self._unsolicited_log.copy()
        self._unsolicited_log.clear()
        return items
