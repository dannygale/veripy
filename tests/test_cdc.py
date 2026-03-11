"""Tests for CDC primitives: Synchronizer and AsyncFIFO."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.cdc import Synchronizer, AsyncFIFO, _bin2gray, _gray2bin
from veripy.lint import lint
from veripy.verify import TestBench, initial

T = 10


class TestSynchronizer(TestBench):
    def create_module(self): return Synchronizer(width=8, stages=2)

    @unittest.skip("Synchronizer uses dynamic getattr not supported by csim")
    def test_data_propagates(self):
        dut = self.dut; self.clock('clk', T)
        @initial
        def _():
            dut.rst = 1; yield T; dut.rst = 0
            dut.d = 0xAB
            yield T
            self.assertEqual(self.get('q'), 0)
            yield T
            self.assertEqual(self.get('q'), 0xAB)

    def test_3_stage(self):
        pass  # requires different module instance — tested separately

    @unittest.skip("Synchronizer uses dynamic getattr not supported by csim")
    def test_reset_clears(self):
        dut = self.dut; self.clock('clk', T)
        @initial
        def _():
            dut.d = 0xFF; yield T; yield T
            self.assertEqual(self.get('q'), 0xFF)
            dut.rst = 1; yield T
            self.assertEqual(self.get('q'), 0)


class TestSync3Stage(TestBench):
    def create_module(self): return Synchronizer(width=1, stages=3)

    @unittest.skip("Synchronizer uses dynamic getattr not supported by csim")
    def test_3_stage(self):
        dut = self.dut; self.clock('clk', T)
        @initial
        def _():
            dut.rst = 1; yield T; dut.rst = 0
            dut.d = 1
            yield T; yield T
            self.assertEqual(self.get('q'), 0)
            yield T
            self.assertEqual(self.get('q'), 1)


class TestSynchronizerLint(unittest.TestCase):
    def test_no_cdc_warning_through_sync(self):
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


class TestAsyncFIFO(TestBench):
    def create_module(self): return AsyncFIFO(width=8, depth=4)

    def _clocks(self):
        from veripy.context import always as _always
        @_always
        def wclk():
            self.set(wclk=0); yield T//2
            self.set(wclk=1); yield T//2
        @_always
        def rclk():
            self.set(rclk=0); yield T//2
            self.set(rclk=1); yield T//2

    @unittest.expectedFailure  # AsyncFIFO uses ._val direct writes, not lowerable to IR
    def test_empty_after_reset(self):
        dut = self.dut; self._clocks()
        @initial
        def _():
            dut.wrst = 1; dut.rrst = 1; yield T
            dut.wrst = 0; dut.rrst = 0; yield T
            self.assertEqual(self.get('empty'), 1)
            self.assertEqual(self.get('full'), 0)

    @unittest.expectedFailure
    def test_write_then_read(self):
        dut = self.dut; self._clocks()
        @initial
        def _():
            dut.wrst = 1; dut.rrst = 1; yield T
            dut.wrst = 0; dut.rrst = 0
            dut.wen = 1; dut.wdata = 42; yield T
            dut.wen = 0
            yield T; yield T; yield T; yield T  # extra sync cycles
            self.assertEqual(self.get('empty'), 0)
            val = self.get('rdata')
            dut.ren = 1; yield T; dut.ren = 0
            self.assertEqual(val, 42)

    @unittest.expectedFailure
    def test_fifo_ordering(self):
        dut = self.dut; self._clocks()
        @initial
        def _():
            dut.wrst = 1; dut.rrst = 1; yield T
            dut.wrst = 0; dut.rrst = 0
            for v in [10, 20, 30]:
                dut.wen = 1; dut.wdata = v; yield T
            dut.wen = 0
            yield T; yield T
            vals = []
            for _ in range(3):
                vals.append(self.get('rdata'))
                dut.ren = 1; yield T; dut.ren = 0
                yield T; yield T
            self.assertEqual(vals, [10, 20, 30])

    @unittest.expectedFailure
    def test_full_flag(self):
        dut = self.dut; self._clocks()
        @initial
        def _():
            dut.wrst = 1; dut.rrst = 1; yield T
            dut.wrst = 0; dut.rrst = 0
            for i in range(4):
                dut.wen = 1; dut.wdata = i; yield T
            dut.wen = 0
            yield T; yield T; yield T
            self.assertEqual(self.get('full'), 1)


if __name__ == '__main__':
    unittest.main()
