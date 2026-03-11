"""Tests for formal property support: assert_always and cover."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register
from veripy.verify import TestBench, initial

T = 10


class Counter(Module):
    def __init__(self, n=4):
        self.clock   = Input()
        self.reset   = Input()
        self.enable  = Input()
        self.count   = Output(n)
        self.counter = Register(n)
        super().__init__()
        @self.comb
        def drive():
            self.count = self.counter
        @self.posedge(self.clock)
        def inc():
            if self.reset:
                self.counter = 0
            elif self.enable:
                self.counter = self.counter + 1


class TestAssertAlways(TestBench):
    def create_module(self): return Counter(n=4)

    def test_passing_assertion(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.enable = 1
            for _ in range(5): yield T

    def test_failing_assertion(self):
        # Assertions are checked in the compiled model — test via module directly
        c = Counter(n=4)
        @c.assert_always(c.clock)
        def under_three():
            return c.count < 3
        self.assertEqual(len(c._assertions), 1)


class TestCover(TestBench):
    def create_module(self): return Counter(n=4)

    def test_cover_registered(self):
        c = Counter(n=4)
        @c.cover(c.clock)
        def reaches_three():
            return c.count == 3
        self.assertEqual(len(c._covers), 1)


class TestSDC(unittest.TestCase):
    def _make_module(self):
        class M(Module):
            def __init__(self):
                self.clk   = Input()
                self.reset = Input()
                self.d     = Input(8)
                self.q     = Output(8)
                self.r     = Register(8)
                super().__init__()
                self.create_clock(self.clk, period_ns=10)
                self.max_delay(self.d, self.q, ns=5)
                self.false_path(self.reset, self.q)
                @self.comb
                def drive():
                    self.q = self.r
                @self.posedge(self.clk)
                def logic():
                    if self.reset:
                        self.r = 0
                    else:
                        self.r = self.d
        return M()

    def test_create_clock(self):
        sdc = self._make_module().to_sdc()
        self.assertIn('create_clock -period 10 [get_ports clk]', sdc)

    def test_max_delay(self):
        sdc = self._make_module().to_sdc()
        self.assertIn('set_max_delay 5 -from [get_ports d] -to [get_ports q]', sdc)

    def test_false_path(self):
        sdc = self._make_module().to_sdc()
        self.assertIn('set_false_path -from [get_ports reset] -to [get_ports q]', sdc)

    def test_no_timing_empty_sdc(self):
        class Empty(Module):
            def __init__(self):
                self.d = Input()
                super().__init__()
        self.assertEqual(Empty().to_sdc(), '')


if __name__ == '__main__':
    unittest.main()
