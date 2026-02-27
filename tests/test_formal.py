"""Tests for formal property support: assert_always and cover."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register


class Counter(Module):
    def __init__(self, n=4):
        self.clock  = Input()
        self.reset  = Input()
        self.enable = Input()
        self.count  = Output(n)
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


class TestAssertAlways(unittest.TestCase):
    def test_passing_assertion(self):
        c = Counter(n=4)
        @c.assert_always(c.clock)
        def always_true():
            return True
        c.reset.set(1); c.tick(); c.reset.set(0)
        c.enable.set(1)
        c.simulate(5)  # should not raise

    def test_failing_assertion(self):
        c = Counter(n=4)
        @c.assert_always(c.clock)
        def under_three():
            return c.count < 3
        c.reset.set(1); c.enable.set(1); c.tick(); c.reset.set(0)
        with self.assertRaises(AssertionError) as ctx:
            c.simulate(10)
        self.assertIn('under_three', str(ctx.exception))


class TestCover(unittest.TestCase):
    def test_cover_hit(self):
        c = Counter(n=4)
        @c.cover(c.clock)
        def reaches_three():
            return c.count == 3
        c.reset.set(1); c.enable.set(1); c.tick(); c.reset.set(0)
        c.simulate(5)
        self.assertTrue(c._covers[0][2][0])

    def test_cover_not_hit(self):
        c = Counter(n=4)
        @c.cover(c.clock)
        def reaches_twenty():
            return c.count == 20  # impossible for 4-bit
        c.reset.set(1); c.enable.set(1); c.tick(); c.reset.set(0)
        c.simulate(5)
        self.assertFalse(c._covers[0][2][0])


if __name__ == '__main__':
    unittest.main()
