"""Tests for Counter module: simulation correctness + Verilog output."""

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


class TestCounterSim(TestBench):
    def create_module(self): return Counter(n=4)

    def test_reset(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T
            dut.reset = 0
            self.assertEqual(self.get('counter'), 0)
            self.assertEqual(self.get('count'), 0)

    def test_counts_up(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T
            dut.reset = 0
            for i in range(1, 6):
                yield T
                self.assertEqual(self.get('counter'), i)

    def test_wraps_at_width(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T
            dut.reset = 0
            for _ in range(15): yield T
            self.assertEqual(self.get('counter'), 15)
            yield T
            self.assertEqual(self.get('counter'), 0)

    def test_enable_gate(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T
            dut.reset = 0; yield T
            self.assertEqual(self.get('counter'), 1)
            dut.enable = 0; yield T; yield T
            self.assertEqual(self.get('counter'), 1)
            dut.enable = 1; yield T
            self.assertEqual(self.get('counter'), 2)

    def test_output_tracks_counter(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T
            dut.reset = 0
            for _ in range(5):
                yield T
                self.assertEqual(self.get('count'), self.get('counter'))

    def test_reset_during_count(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T
            dut.reset = 0
            for _ in range(5): yield T
            self.assertNotEqual(self.get('counter'), 0)
            dut.reset = 1; yield T
            self.assertEqual(self.get('counter'), 0)


class TestCounterVerilog(unittest.TestCase):
    def setUp(self):
        self.v = Counter(n=8).to_verilog()

    def test_module_declaration(self):
        self.assertIn('module counter', self.v)
        self.assertIn('endmodule', self.v)

    def test_ports(self):
        self.assertIn('input clock', self.v)
        self.assertIn('input reset', self.v)
        self.assertIn('input enable', self.v)
        self.assertIn('output [7:0] count', self.v)

    def test_register(self):
        self.assertIn('reg [7:0] counter', self.v)

    def test_assign(self):
        self.assertIn('assign count = counter;', self.v)

    def test_always_posedge(self):
        self.assertIn('always @(posedge clock)', self.v)

    def test_reset_logic(self):
        self.assertIn('if (reset)', self.v)
        self.assertIn('counter <= 0;', self.v)

    def test_increment(self):
        self.assertIn('counter <= (counter + 1);', self.v)

    def test_enable_gate(self):
        self.assertIn('if (enable)', self.v)


if __name__ == '__main__':
    unittest.main()
