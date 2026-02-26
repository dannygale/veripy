"""Tests for Counter module: simulation correctness + Verilog output."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.counter import Counter


class TestCounterSim(unittest.TestCase):
    def setUp(self):
        self.c = Counter(n=4)
        self.c.enable._val = 1

    def _reset(self, cycles=1):
        self.c.reset._val = 1
        for _ in range(cycles):
            self.c.tick()
        self.c.reset._val = 0

    def test_reset(self):
        self._reset()
        self.assertEqual(self.c.counter._val, 0)
        self.assertEqual(self.c.count._val, 0)

    def test_counts_up(self):
        self._reset()
        for i in range(1, 6):
            self.c.tick()
            self.assertEqual(self.c.counter._val, i)

    def test_wraps_at_width(self):
        self._reset()
        self.c.simulate(15)
        self.assertEqual(self.c.counter._val, 15)
        self.c.tick()
        self.assertEqual(self.c.counter._val, 0)  # 4-bit wrap

    def test_enable_gate(self):
        self._reset()
        self.c.tick()
        self.assertEqual(self.c.counter._val, 1)
        self.c.enable._val = 0
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.counter._val, 1)  # frozen
        self.c.enable._val = 1
        self.c.tick()
        self.assertEqual(self.c.counter._val, 2)  # resumes

    def test_output_tracks_counter(self):
        self._reset()
        for _ in range(5):
            self.c.tick()
            self.assertEqual(self.c.count._val, self.c.counter._val)

    def test_reset_during_count(self):
        self._reset()
        self.c.simulate(5)
        self.assertNotEqual(self.c.counter._val, 0)
        self._reset()
        self.assertEqual(self.c.counter._val, 0)


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
