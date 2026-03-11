"""Tests for reactive yields: until() and timeout."""
import unittest

from veripy import Module, Input, Output, Register, posedge, until
from veripy.verify import TestBench, initial


class Handshake(Module):
    def __init__(self):
        self.clock = Input()
        self.enable = Input()
        self.ready = Output()
        self.cnt = Register(8)
        super().__init__()

        @self.comb
        def drive():
            self.ready = 1 if int(self.cnt) >= 5 else 0

        @self.posedge(self.clock)
        def count():
            if self.enable:
                self.cnt = self.cnt + 1


class Counter(Module):
    def __init__(self):
        self.clock = Input()
        self.cnt = Register(8)
        self.out = Output(8)
        super().__init__()
        @self.comb
        def drive():
            self.out = self.cnt
        @self.posedge(self.clock)
        def inc():
            self.cnt = self.cnt + 1


class TestUntil(TestBench):
    def create_module(self): return Handshake()

    def test_wait_for_condition(self):
        dut = self.dut; self.clock('clock', 10)
        ready_time = []
        @initial
        def stim():
            dut.enable = 1
            yield until(lambda: self.get('ready') == 1)
            ready_time.append(self._engine.time)
            self.assertGreater(ready_time[0], 0)
            self.assertEqual(self.get('ready'), 1)

    def test_timeout_fires(self):
        dut = self.dut; self.clock('clock', 10)
        timed_out = []
        @initial
        def stim():
            dut.enable = 0
            try:
                yield until(lambda: self.get('ready') == 1, timeout=100)
            except TimeoutError:
                timed_out.append(self._engine.time)
            self.assertEqual(timed_out, [100])

    def test_yield_after_until(self):
        dut = self.dut; self.clock('clock', 10)
        events = []
        @initial
        def stim():
            dut.enable = 1
            yield until(lambda: self.get('ready') == 1)
            events.append(('ready', self._engine.time))
            yield 20
            events.append(('after', self._engine.time))
            self.assertEqual(len(events), 2)
            self.assertEqual(events[1][0], 'after')
            self.assertEqual(events[1][1], events[0][1] + 20)


class TestFork(TestBench):
    def create_module(self): return Counter()

    def test_fork_join_all(self):
        self.clock('clock', 10)
        events = []
        @initial
        def stim():
            def wait3():
                yield until(lambda: self.get('cnt') >= 3)
                events.append('a')
            def wait5():
                yield until(lambda: self.get('cnt') >= 5)
                events.append('b')
            yield self.fork(wait3, wait5)
            events.append('done')
            self.assertEqual(events, ['a', 'b', 'done'])

    def test_fork_any(self):
        self.clock('clock', 10)
        events = []
        @initial
        def stim():
            def fast():
                yield until(lambda: self.get('cnt') >= 2)
                events.append('fast')
            def slow():
                yield until(lambda: self.get('cnt') >= 100)
                events.append('slow')
            yield self.fork_any(fast, slow)
            events.append('done')
            self.assertEqual(events, ['fast', 'done'])

    def test_fork_with_delays(self):
        self.clock('clock', 10)
        events = []
        @initial
        def stim():
            def block_a():
                yield 30
                events.append(('a', self._engine.time))
            def block_b():
                yield 50
                events.append(('b', self._engine.time))
            yield self.fork(block_a, block_b)
            events.append(('done', self._engine.time))
            self.assertEqual(events[0], ('a', 30))
            self.assertEqual(events[1], ('b', 50))
            self.assertEqual(events[2][0], 'done')


if __name__ == '__main__':
    unittest.main()
