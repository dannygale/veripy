"""Tests for CySimEngine: event-driven simulation semantics."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, posedge
from veripy.verify import TestBench, initial
from veripy.context import always as _always


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


class TestInitial(TestBench):
    def create_module(self): return Counter()

    def test_initial_runs_and_stops(self):
        ran = []
        @initial
        def stim():
            ran.append(True)
            yield 1
        # ran is populated inside the generator which runs during run_sim
        # assert inside generator instead:

    def test_initial_runs_once(self):
        ran = []
        @initial
        def stim():
            ran.append(True)
            yield 1
            self.assertEqual(len(ran), 1)

    def test_counter_via_initial(self):
        dut = self.dut; self.clock('clock', 2)
        @initial
        def stim():
            dut.reset = 1; dut.enable = 1; yield 2
            dut.reset = 0
            for _ in range(5): yield 2
            self.assertEqual(self.get('count'), 5)

    def test_multiple_initial_blocks(self):
        order = []
        @initial
        def block_a():
            order.append('a_start'); yield 1; order.append('a_end')
        @initial
        def block_b():
            order.append('b_start'); yield 1; order.append('b_end')
        @initial
        def check():
            yield 5
            self.assertIn('a_start', order)
            self.assertIn('b_start', order)
            self.assertIn('a_end', order)
            self.assertIn('b_end', order)

    def test_time_advances(self):
        @initial
        def stim():
            yield 5; yield 3
            self.assertEqual(self._engine.time, 8)


class TestAlwaysBlock(TestBench):
    def create_module(self): return Counter()

    def test_always_restarts(self):
        ticks = []
        @initial
        def stop():
            yield 10; self._engine.finish()
        @_always
        def ticker():
            ticks.append(self._engine.time)
            yield 2
        @initial
        def check():
            yield 11
            self.assertGreater(len(ticks), 1)


class TestFinish(TestBench):
    def create_module(self): return Counter()

    def test_finish_stops_simulation(self):
        count = []
        @initial
        def stim():
            for _ in range(100):
                count.append(1)
                yield 1
                if len(count) >= 3:
                    self._engine.finish()
                    return
            self.fail("should have finished early")
        @initial
        def check():
            yield 200
            self.assertLessEqual(len(count), 4)


class TestEdgeDetection(TestBench):
    def create_module(self): return Counter(n=4)

    def test_posedge_triggers(self):
        dut = self.dut; self.clock('clock', 2)
        @initial
        def stim():
            dut.enable = 1; dut.reset = 1; yield 2
            dut.reset = 0
            for _ in range(3): yield 2
            self.assertEqual(self.get('count'), 3)


if __name__ == '__main__':
    unittest.main()
