"""Tests for Counter module: simulation correctness + Verilog output."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.counter import Counter
from veripy.sim import SimEngine

T = 10  # clock period


class TestCounterSim(unittest.TestCase):
    def test_reset(self):
        c = Counter(n=4)
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.enable.set(1); c.reset.set(1)
            yield T
            c.reset.set(0)
            self.assertEqual(int(c.counter), 0)
            self.assertEqual(int(c.count), 0)
        sim.run()

    def test_counts_up(self):
        c = Counter(n=4)
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.enable.set(1); c.reset.set(1)
            yield T
            c.reset.set(0)
            for i in range(1, 6):
                yield T
                self.assertEqual(int(c.counter), i)
        sim.run()

    def test_wraps_at_width(self):
        c = Counter(n=4)
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.enable.set(1); c.reset.set(1)
            yield T
            c.reset.set(0)
            for _ in range(15):
                yield T
            self.assertEqual(int(c.counter), 15)
            yield T
            self.assertEqual(int(c.counter), 0)
        sim.run()

    def test_enable_gate(self):
        c = Counter(n=4)
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.enable.set(1); c.reset.set(1)
            yield T
            c.reset.set(0)
            yield T
            self.assertEqual(int(c.counter), 1)
            c.enable.set(0)
            yield T; yield T
            self.assertEqual(int(c.counter), 1)
            c.enable.set(1)
            yield T
            self.assertEqual(int(c.counter), 2)
        sim.run()

    def test_output_tracks_counter(self):
        c = Counter(n=4)
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.enable.set(1); c.reset.set(1)
            yield T
            c.reset.set(0)
            for _ in range(5):
                yield T
                self.assertEqual(int(c.count), int(c.counter))
        sim.run()

    def test_reset_during_count(self):
        c = Counter(n=4)
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.enable.set(1); c.reset.set(1)
            yield T
            c.reset.set(0)
            for _ in range(5):
                yield T
            self.assertNotEqual(int(c.counter), 0)
            c.reset.set(1)
            yield T
            self.assertEqual(int(c.counter), 0)
        sim.run()


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
