"""Tests for IP library: SyncFifo, EdgeDetector, Debouncer."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import VeripyTestCase, SyncFifo, EdgeDetector, Debouncer, RoundRobinArbiter

T = 5  # half-period


# ── SyncFifo tests ───────────────────────────────────────────────────

class TestSyncFifoBasic(VeripyTestCase):
    def create_module(self):
        return SyncFifo(width=8, depth=4)

    def test_push_pop(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, push=0, pop=0, din=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # Should be empty
            self.assertEqual(self.out('empty'), 1)
            self.assertEqual(self.out('full'), 0)

            # Push 0x42
            self.set(push=1, din=0x42)
            yield T * 2
            self.set(push=0)
            yield T * 2

            # Not empty anymore
            self.assertEqual(self.out('empty'), 0)
            self.assertEqual(self.out('dout'), 0x42)

            # Pop
            self.set(pop=1)
            yield T * 2
            self.set(pop=0)
            yield T * 2

            # Empty again
            self.assertEqual(self.out('empty'), 1)


class TestSyncFifoFull(VeripyTestCase):
    def create_module(self):
        return SyncFifo(width=8, depth=4)

    def test_fill_to_full(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, push=0, pop=0, din=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # Push 4 items
            for i in range(4):
                self.set(push=1, din=i + 1)
                yield T * 2
            self.set(push=0)
            yield T * 2

            # Should be full
            self.assertEqual(self.out('full'), 1)
            self.assertEqual(self.out('count'), 4)


# ── EdgeDetector tests ───────────────────────────────────────────────

class TestEdgeDetector(VeripyTestCase):
    def create_module(self):
        return EdgeDetector()

    def test_rising_edge(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, d=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # d goes 0→1
            self.set(d=1)
            yield T * 2  # posedge: detects rise, prev<=1, rise_r<=1

            self.assertEqual(self.out('rise'), 1)
            self.assertEqual(self.out('fall'), 0)

            # Next cycle: d=1, prev=1 → rise_r<=0
            yield T * 2
            self.assertEqual(self.out('rise'), 0)

    def test_falling_edge(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, d=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # Set d=1, wait for prev to catch up
            self.set(d=1)
            yield T * 4  # two cycles: prev=1

            # d goes 1→0
            self.set(d=0)
            yield T * 2  # posedge: detects fall (d=0, prev=1)

            self.assertEqual(self.out('fall'), 1)
            self.assertEqual(self.out('rise'), 0)


# ── Debouncer tests ──────────────────────────────────────────────────

class TestDebouncer(VeripyTestCase):
    def create_module(self):
        return Debouncer(threshold=3)

    def test_stable_input_passes(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, d=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # q should be 0
            self.assertEqual(self.out('q'), 0)

            # Set d=1 and hold for threshold cycles
            self.set(d=1)
            for _ in range(4):
                yield T * 2

            # After threshold, q should be 1
            self.assertEqual(self.out('q'), 1)

    def test_glitch_rejected(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, d=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # Brief glitch: d=1 for 1 cycle, then back to 0
            self.set(d=1)
            yield T * 2
            self.set(d=0)
            yield T * 4

            # q should still be 0 (glitch rejected)
            self.assertEqual(self.out('q'), 0)


# ── Verilog emission tests ───────────────────────────────────────────

class TestRoundRobinArbiter(VeripyTestCase):
    def create_module(self):
        return RoundRobinArbiter(n=4)

    def test_single_request(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, req=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # Request bit 0 only
            self.set(req=0b0001)
            yield T * 2  # posedge: grnt <= 0b0001
            self.assertEqual(self.out('grant'), 0b0001)

    def test_round_robin_rotation(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, req=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # All 4 requesting — should rotate
            self.set(req=0b1111)
            yield T * 2  # posedge: ptr=0 grants bit 0, ptr<=1
            self.assertEqual(self.out('grant'), 0b0001)

            yield T * 2  # posedge: ptr=1 grants bit 1, ptr<=2
            self.assertEqual(self.out('grant'), 0b0010)

            yield T * 2  # posedge: ptr=2 grants bit 2, ptr<=3
            self.assertEqual(self.out('grant'), 0b0100)

    def test_no_request(self):
        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            self.set(reset=1, req=0)
            yield T * 4
            self.set(reset=0)
            yield T * 2

            # No requests
            self.set(req=0)
            yield T * 2
            self.assertEqual(self.out('grant'), 0)


class TestIPVerilog(unittest.TestCase):
    def test_syncfifo_emits(self):
        v = SyncFifo(width=8, depth=4).to_verilog()
        self.assertIn('module syncfifo', v)
        self.assertIn('always @(posedge clock)', v)
        self.assertIn('mem', v)

    def test_edgedetector_emits(self):
        v = EdgeDetector().to_verilog()
        self.assertIn('module edgedetector', v)
        self.assertIn('rise', v)
        self.assertIn('fall', v)

    def test_debouncer_emits(self):
        v = Debouncer(threshold=8).to_verilog()
        self.assertIn('module debouncer', v)
        self.assertIn('threshold', v)

    def test_arbiter_emits(self):
        v = RoundRobinArbiter(n=4).to_verilog()
        self.assertIn('module roundrobinarbiter', v)
        self.assertIn('grant', v)
        self.assertIn('req', v)


if __name__ == '__main__':
    unittest.main()
