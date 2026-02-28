"""Tests for CDC primitives: Synchronizer and AsyncFIFO."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.cdc import Synchronizer, AsyncFIFO, _bin2gray, _gray2bin
from veripy.lint import lint
from veripy.sim import SimEngine

T = 10


class TestSynchronizer(unittest.TestCase):
    def test_data_propagates(self):
        s = Synchronizer(width=8, stages=2)
        sim = SimEngine(s)
        sim.clock(s.clk, T)
        @sim.initial
        def _():
            s.rst.set(1); yield T; s.rst.set(0)
            s.d.set(0xAB)
            yield T  # stage0 = 0xAB, stage1 = 0
            self.assertEqual(int(s.q), 0)
            yield T  # stage0 = 0xAB, stage1 = 0xAB
            self.assertEqual(int(s.q), 0xAB)
        sim.run()

    def test_3_stage(self):
        s = Synchronizer(width=1, stages=3)
        sim = SimEngine(s)
        sim.clock(s.clk, T)
        @sim.initial
        def _():
            s.rst.set(1); yield T; s.rst.set(0)
            s.d.set(1)
            yield T; yield T
            self.assertEqual(int(s.q), 0)  # not through yet
            yield T
            self.assertEqual(int(s.q), 1)
        sim.run()

    def test_reset_clears(self):
        s = Synchronizer(width=8)
        sim = SimEngine(s)
        sim.clock(s.clk, T)
        @sim.initial
        def _():
            s.d.set(0xFF)
            yield T; yield T
            self.assertEqual(int(s.q), 0xFF)
            s.rst.set(1); yield T
            self.assertEqual(int(s.q), 0)
        sim.run()

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
        for bits in (3, 4, 5):
            for i in range(1, 1 << bits):
                g0 = _bin2gray(i - 1, bits)
                g1 = _bin2gray(i, bits)
                diff = g0 ^ g1
                self.assertEqual(diff & (diff - 1), 0)


class TestAsyncFIFO(unittest.TestCase):
    def test_empty_after_reset(self):
        f = AsyncFIFO(width=8, depth=4)
        sim = SimEngine(f)
        sim.clock(f.wclk, T)
        sim.clock(f.rclk, T)
        @sim.initial
        def _():
            f.wrst.set(1); f.rrst.set(1); yield T
            f.wrst.set(0); f.rrst.set(0); yield T
            self.assertEqual(int(f.empty), 1)
            self.assertEqual(int(f.full), 0)
        sim.run()

    def test_write_then_read(self):
        f = AsyncFIFO(width=8, depth=4)
        sim = SimEngine(f)
        sim.clock(f.wclk, T)
        sim.clock(f.rclk, T)
        @sim.initial
        def _():
            f.wrst.set(1); f.rrst.set(1); yield T
            f.wrst.set(0); f.rrst.set(0)
            # Write
            f.wen.set(1); f.wdata.set(42); yield T
            f.wen.set(0)
            # Sync ticks
            yield T; yield T
            self.assertEqual(int(f.empty), 0)
            # Read
            val = int(f.rdata)
            f.ren.set(1); yield T; f.ren.set(0)
            self.assertEqual(val, 42)
        sim.run()

    def test_fifo_ordering(self):
        f = AsyncFIFO(width=8, depth=4)
        sim = SimEngine(f)
        sim.clock(f.wclk, T)
        sim.clock(f.rclk, T)
        @sim.initial
        def _():
            f.wrst.set(1); f.rrst.set(1); yield T
            f.wrst.set(0); f.rrst.set(0)
            for v in [10, 20, 30]:
                f.wen.set(1); f.wdata.set(v); yield T
            f.wen.set(0)
            yield T; yield T  # sync
            vals = []
            for _ in range(3):
                vals.append(int(f.rdata))
                f.ren.set(1); yield T; f.ren.set(0)
                yield T; yield T  # sync
            self.assertEqual(vals, [10, 20, 30])
        sim.run()

    def test_full_flag(self):
        f = AsyncFIFO(width=8, depth=4)
        sim = SimEngine(f)
        sim.clock(f.wclk, T)
        sim.clock(f.rclk, T)
        @sim.initial
        def _():
            f.wrst.set(1); f.rrst.set(1); yield T
            f.wrst.set(0); f.rrst.set(0)
            for i in range(4):
                f.wen.set(1); f.wdata.set(i); yield T
            f.wen.set(0)
            yield T; yield T; yield T  # sync
            self.assertEqual(int(f.full), 1)
        sim.run()


if __name__ == '__main__':
    unittest.main()
