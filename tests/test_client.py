"""
Tests for the client layer: _dispatch_frame routing, send_command behaviour,
and the UART frame accumulator.

uart_client imports serial_asyncio (a C extension).  We stub it out at the
module level so these tests run without hardware or the package installed.
"""

import asyncio
import sys
import unittest
from unittest.mock import MagicMock, patch

# Stub serial_asyncio before importing uart_client so the import succeeds
# in environments where pyserial-asyncio is not installed.
sys.modules.setdefault("serial_asyncio", MagicMock())

from pyam3.client import Mower, _frame_length, MAX_FRAME_SIZE     # noqa: E402
from pyam3.uart_client import _UartProtocol                       # noqa: E402
from pyam3.protocol.common import crc                             # noqa: E402


# ── Test helpers ──────────────────────────────────────────────────────────────

class _MockMower(Mower):
    """Minimal concrete Mower that records outgoing bytes instead of sending."""

    def __init__(self):
        super().__init__()
        self.sent: list[bytearray] = []
        self._connected = True

    async def connect(self):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    def _send_bytes(self, data: bytearray) -> None:
        self.sent.append(bytearray(data))


# ── Known valid frames (sourced from existing protocol tests) ─────────────────

# Extended protocol: IsOperatorLoggedIn response, tid=3, data=0
# Note: byte[4]=0x03 is the transaction ID — 0x03 also equals ETX, making
# this a good regression target for the "ETX in payload" scenario.
EXTENDED_FRAME = bytearray.fromhex("02810800039239020000da03")

# Simple protocol: GetBatteryData response
SIMPLE_FRAME = bytearray.fromhex(
    "02151500a24bbb04bcfbdc0040060000000000000cfe00006203"
)

# Linked protocol: StateEvent (state=7)
LINKED_FRAME = bytearray.fromhex("02fd10000000000001fe02ea1101000100073b03")


# ── _frame_length ─────────────────────────────────────────────────────────────

class TestFrameLength(unittest.TestCase):

    def test_extended(self):
        # remaining=8, total=12
        self.assertEqual(_frame_length(EXTENDED_FRAME), 12)

    def test_linked(self):
        # remaining=16, total=20
        self.assertEqual(_frame_length(LINKED_FRAME), 20)

    def test_simple(self):
        # payload_len=0x15=21, total=26
        self.assertEqual(_frame_length(SIMPLE_FRAME), 26)

    def test_too_short_returns_none(self):
        self.assertIsNone(_frame_length(bytearray()))
        self.assertIsNone(_frame_length(bytearray([0x02])))
        self.assertIsNone(_frame_length(bytearray([0x02, 0x81])))

    def test_extended_needs_4_bytes_for_length_field(self):
        # Only 3 bytes available — can't yet read the 2-byte remaining field
        self.assertIsNone(_frame_length(bytearray([0x02, 0x81, 0x08])))

    def test_linked_needs_4_bytes_for_length_field(self):
        self.assertIsNone(_frame_length(bytearray([0x02, 0xFD, 0x10])))

    def test_simple_computable_from_3_bytes(self):
        # Simple protocol length is at byte[2], so 3 bytes is enough
        buf = bytearray([0x02, 0x15, 0x15])   # payload_len=21, total=26
        self.assertEqual(_frame_length(buf), 26)


# ── UART frame accumulator ────────────────────────────────────────────────────

class TestUartAccumulator(unittest.TestCase):

    def _make_protocol(self):
        frames = []
        proto = _UartProtocol(
            lambda f: frames.append(bytearray(f)),
            lambda exc: None,
        )
        return proto, frames

    def test_complete_frame_in_one_chunk(self):
        proto, frames = self._make_protocol()
        proto.data_received(EXTENDED_FRAME)
        self.assertEqual(frames, [EXTENDED_FRAME])

    def test_frame_split_across_two_chunks(self):
        proto, frames = self._make_protocol()
        mid = len(EXTENDED_FRAME) // 2
        proto.data_received(EXTENDED_FRAME[:mid])
        self.assertEqual(frames, [])                    # incomplete, no dispatch yet
        proto.data_received(EXTENDED_FRAME[mid:])
        self.assertEqual(frames, [EXTENDED_FRAME])

    def test_frame_arrives_byte_by_byte(self):
        proto, frames = self._make_protocol()
        for byte in EXTENDED_FRAME:
            proto.data_received(bytes([byte]))
        self.assertEqual(frames, [EXTENDED_FRAME])

    def test_two_frames_in_one_chunk(self):
        proto, frames = self._make_protocol()
        proto.data_received(EXTENDED_FRAME + SIMPLE_FRAME)
        self.assertEqual(frames, [EXTENDED_FRAME, SIMPLE_FRAME])

    def test_garbage_before_stx_is_discarded(self):
        proto, frames = self._make_protocol()
        proto.data_received(bytes([0xAA, 0xBB, 0xCC]) + EXTENDED_FRAME)
        self.assertEqual(frames, [EXTENDED_FRAME])

    def test_no_stx_clears_buffer(self):
        proto, frames = self._make_protocol()
        proto.data_received(bytes([0xAA, 0xBB, 0xCC]))
        self.assertEqual(frames, [])
        self.assertEqual(len(proto._accumulator._buf), 0)

    def test_etx_byte_in_payload_does_not_truncate_frame(self):
        # EXTENDED_FRAME has 0x03 (ETX) at byte[4] (the transaction ID).
        # A naive scan for ETX would stop there and return a 5-byte fragment.
        # The length-based accumulator must return the full 12-byte frame.
        proto, frames = self._make_protocol()
        proto.data_received(EXTENDED_FRAME)
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(frames[0]), 12)            # full frame, not 5

    def test_oversized_frame_discarded_and_accumulator_resyncs(self):
        # Craft a frame whose length field claims more than MAX_FRAME_SIZE bytes.
        # 800 = 0x0320 (lo=0x20, hi=0x03) — neither byte is 0x02 (STX),
        # so the resync scan correctly skips the garbage and finds EXTENDED_FRAME.
        bad_len = 800
        self.assertGreater(bad_len, MAX_FRAME_SIZE)
        bad_frame = bytearray([
            0x02, 0x81,
            bad_len & 0xFF,         # 0x20
            (bad_len >> 8) & 0xFF,  # 0x03
        ])
        proto, frames = self._make_protocol()
        proto.data_received(bad_frame + EXTENDED_FRAME)
        self.assertEqual(frames, [EXTENDED_FRAME])

    def test_connection_lost_clears_buffer(self):
        proto, frames = self._make_protocol()
        proto.data_received(EXTENDED_FRAME[:4])         # partial frame
        self.assertGreater(len(proto._accumulator._buf), 0)
        proto.connection_lost(None)
        self.assertEqual(len(proto._accumulator._buf), 0)

    def test_three_different_protocol_frames_in_sequence(self):
        proto, frames = self._make_protocol()
        proto.data_received(EXTENDED_FRAME + LINKED_FRAME + SIMPLE_FRAME)
        self.assertEqual(frames, [EXTENDED_FRAME, LINKED_FRAME, SIMPLE_FRAME])


# ── Mower._dispatch_frame ─────────────────────────────────────────────────────

class TestDispatchFrame(unittest.IsolatedAsyncioTestCase):

    async def test_extended_resolves_pending_future(self):
        mower = _MockMower()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        mower._pending[3] = fut             # tid=3 matches EXTENDED_FRAME byte[4]
        mower._dispatch_frame(EXTENDED_FRAME)
        self.assertTrue(fut.done())
        self.assertEqual(fut.result(), EXTENDED_FRAME)

    async def test_extended_unsolicited_does_not_raise(self):
        mower = _MockMower()
        mower._dispatch_frame(EXTENDED_FRAME)   # no future registered

    async def test_extended_too_short_does_not_raise(self):
        mower = _MockMower()
        mower._dispatch_frame(bytearray([0x02, 0x81, 0x00]))   # < 5 bytes

    async def test_extended_already_done_future_not_set_again(self):
        mower = _MockMower()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.cancel()
        mower._pending[3] = fut
        mower._dispatch_frame(EXTENDED_FRAME)   # should not raise InvalidStateError

    async def test_linked_fires_event_callback(self):
        mower = _MockMower()
        events = []
        mower.on_event(lambda e: events.append(e))
        mower._dispatch_frame(LINKED_FRAME)
        self.assertEqual(len(events), 1)
        self.assertIn("name", events[0])
        self.assertIn("data", events[0])

    async def test_linked_fires_multiple_callbacks(self):
        mower = _MockMower()
        log = []
        mower.on_event(lambda e: log.append("a"))
        mower.on_event(lambda e: log.append("b"))
        mower._dispatch_frame(LINKED_FRAME)
        self.assertEqual(log, ["a", "b"])

    async def test_linked_bad_frame_does_not_raise(self):
        mower = _MockMower()
        mower.on_event(lambda e: None)
        mower._dispatch_frame(bytearray([0x02, 0xFD, 0x00, 0x00, 0x03]))   # malformed

    async def test_linked_callback_exception_does_not_propagate(self):
        mower = _MockMower()
        second_called = []
        mower.on_event(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        mower.on_event(lambda e: second_called.append(True))
        mower._dispatch_frame(LINKED_FRAME)
        self.assertEqual(second_called, [True])     # second callback still ran

    async def test_simple_resolves_pending_future(self):
        mower = _MockMower()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        mower._simple_future = fut
        mower._dispatch_frame(SIMPLE_FRAME)
        self.assertTrue(fut.done())
        self.assertEqual(fut.result(), SIMPLE_FRAME)

    async def test_simple_no_pending_future_does_not_raise(self):
        mower = _MockMower()
        mower._simple_future = None
        mower._dispatch_frame(SIMPLE_FRAME)

    async def test_psk_frame_logs_warning_and_does_not_raise(self):
        mower = _MockMower()
        with self.assertLogs("pyam3.client", level="WARNING") as log:
            mower._dispatch_frame(bytearray([0x02, 0xFE, 0x00, 0x03]))
        self.assertTrue(any("0xFE" in msg or "PSK" in msg for msg in log.output))

    async def test_unknown_marker_does_not_raise(self):
        mower = _MockMower()
        # 0x90 >= 0x80 but not 0x81, 0xFD, or 0xFE
        mower._dispatch_frame(bytearray([0x02, 0x90, 0x00, 0x03]))

    async def test_frame_too_short_does_not_raise(self):
        mower = _MockMower()
        mower._dispatch_frame(bytearray([0x02]))


# ── send_command behaviour ────────────────────────────────────────────────────

class TestSendCommand(unittest.IsolatedAsyncioTestCase):

    async def test_returns_none_when_not_connected(self):
        mower = _MockMower()
        mower._connected = False
        self.assertIsNone(await mower.send_command("GetBatteryLevel"))

    async def test_returns_none_on_timeout(self):
        mower = _MockMower()
        result = await mower.send_command("GetBatteryLevel", timeout=0.05, retries=1)
        self.assertIsNone(result)
        self.assertEqual(len(mower.sent), 1)

    async def test_retries_correct_number_of_times(self):
        mower = _MockMower()
        await mower.send_command("GetBatteryLevel", timeout=0.05, retries=3)
        self.assertEqual(len(mower.sent), 3)

    async def test_extended_command_returns_parsed_data(self):
        # Patch random so the transaction ID is deterministic (=3),
        # then auto-dispatch the matching response frame when bytes are sent.
        mower = _MockMower()
        original_send = mower._send_bytes

        def auto_respond(data: bytearray) -> None:
            original_send(data)
            mower._dispatch_frame(EXTENDED_FRAME)   # tid=3 matches patched randint

        mower._send_bytes = auto_respond
        with patch("random.randint", return_value=3):
            result = await mower.send_command("IsOperatorLoggedIn")
        self.assertEqual(result, 0)

    async def test_simple_command_returns_parsed_data(self):
        mower = _MockMower()
        original_send = mower._send_bytes

        def auto_respond(data: bytearray) -> None:
            original_send(data)
            mower._dispatch_frame(SIMPLE_FRAME)

        mower._send_bytes = auto_respond
        result = await mower.send_command("GetBatteryData")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["batavoltage"], 19362)

    async def test_cancel_pending_cancels_futures(self):
        mower = _MockMower()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        mower._pending[42] = fut
        mower._cancel_pending()
        self.assertTrue(fut.cancelled())
        self.assertEqual(mower._pending, {})


if __name__ == "__main__":
    unittest.main()
