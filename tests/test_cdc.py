"""Tests for CDC primitives: Synchronizer and AsyncFIFO."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.cdc import Synchronizer, AsyncFIFO, _bin2gray, _gray2bin
from veripy.lint import lint


class TestSynchronizer(unittest.TestCase):
    def _tick_sync(self, sync):
        """Tick only the synchronizer's posedge block."""
        sync.tick()

    def test_data_propagates(self):
        s = Synchronizer(width=8, stages=2)
        s.rst.set(1); s.tick(); s.rst.set(0)
        s.d.set(0xAB)
        s.tick()  # stage0 = 0xAB, stage1 = 0
        self.assertEqual(int(s.q), 0)
        s.tick()  # stage0 = 0xAB, stage1 = 0xAB
        self.assertEqual(int(s.q), 0xAB)

    def test_3_stage(self):
        s = Synchronizer(width=1, stages=3)
        s.rst.set(1); s.tick(); s.rst.set(0)
        s.d.set(1)
        s.tick(); s.tick()
        self.assertEqual(int(s.q), 0)  # not through yet
        s.tick()
        self.assertEqual(int(s.q), 1)

    def test_reset_clears(self):
        s = Synchronizer(width=8)
        s.d.set(0xFF)
        s.tick(); s.tick()
        self.assertEqual(int(s.q), 0xFF)
        s.rst.set(1); s.tick()
        self.assertEqual(int(s.q), 0)

    def test_no_cdc_warning_through_sync(self):
        """Using a Synchronizer should not trigger CDC lint."""
        class Design(Module):
            def __init__(self):
                self.fclk = Input()
                self.sclk = Input()
                self.rst  = Input()
                self.d    = Input(8)
                self.q    = Output(8)
                self.rf   = Register(8)
                self.rs   = Register(8)
                self.sync = Synchronizer(width=8)
                super().__init__()

                @self.posedge(self.fclk)
                def fast():
                    self.rf = self.d

                @self.comb
                def wire():
                    self.sync.d = self.rf

                @self.posedge(self.sclk)
                def slow():
                    self.rs = self.sync.q

                @self.comb
                def out():
                    self.q = self.rs

        w = lint(Design())
        cdc = [m for _, m in w if 'CDC' in m]
        self.assertEqual(len(cdc), 0)


class TestGrayCode(unittest.TestCase):
    def test_roundtrip(self):
        for bits in (3, 4, 5):
            for i in range(1 << bits):
                g = _bin2gray(i, bits)
                self.assertEqual(_gray2bin(g, bits), i)

    def test_single_bit_change(self):
        """Adjacent gray codes differ by exactly one bit."""
        for bits in (3, 4, 5):
            for i in range(1, 1 << bits):
                g0 = _bin2gray(i - 1, bits)
                g1 = _bin2gray(i, bits)
                diff = g0 ^ g1
                self.assertEqual(diff & (diff - 1), 0)  # power of 2 = 1 bit


class TestAsyncFIFO(unittest.TestCase):
    def _make_fifo(self, width=8, depth=4):
        return AsyncFIFO(width=width, depth=depth)

    def _reset(self, f):
        f.wrst.set(1); f.rrst.set(1)
        f.tick()
        f.wrst.set(0); f.rrst.set(0)

    def _write(self, f, val):
        f.wen.set(1); f.wdata.set(val)
        f.tick()
        f.wen.set(0)

    def _read(self, f):
        """Read: rdata is combinationally valid at current rptr. Tick advances."""
        f.ren.set(1)
        val = int(f.rdata)  # comb output at current rptr
        f.tick()             # advances rptr
        f.ren.set(0)
        return val

    def test_empty_after_reset(self):
        f = self._make_fifo()
        self._reset(f)
        f.tick()
        self.assertEqual(int(f.empty), 1)
        self.assertEqual(int(f.full), 0)

    def test_write_then_read(self):
        f = self._make_fifo()
        self._reset(f)
        # Write a value
        self._write(f, 42)
        # Need extra ticks for gray pointer sync (2 stages)
        f.tick(); f.tick()
        self.assertEqual(int(f.empty), 0)
        # Read it back
        val = self._read(f)
        self.assertEqual(val, 42)

    def test_fifo_ordering(self):
        f = self._make_fifo(depth=4)
        self._reset(f)
        # Write 3 values
        for v in [10, 20, 30]:
            self._write(f, v)
        # Sync ticks
        f.tick(); f.tick()
        # Read back in order
        vals = []
        for _ in range(3):
            vals.append(self._read(f))
            f.tick(); f.tick()  # sync
        self.assertEqual(vals, [10, 20, 30])

    def test_full_flag(self):
        f = self._make_fifo(depth=4)
        self._reset(f)
        # Fill it up (depth=4, so 4 writes)
        for i in range(4):
            self._write(f, i)
        # Sync ticks for gray pointer propagation
        f.tick(); f.tick()
        f.tick()  # evaluate comb
        self.assertEqual(int(f.full), 1)


if __name__ == '__main__':
    unittest.main()
