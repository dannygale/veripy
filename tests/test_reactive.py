"""Tests for reactive yields: until() and timeout."""
import unittest

from veripy import Module, Input, Output, Register, posedge, VeripyTestCase, until
from veripy.sim import SimEngine


class Handshake(Module):
    """Counter that asserts ready when count >= 5."""
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


class TestUntilSimEngine(unittest.TestCase):
    """Test until() directly with SimEngine."""

    def test_wait_for_condition(self):
        dut = Handshake()
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        ready_time = []

        @sim.initial
        def stim():
            dut.enable.set(1)
            yield until(lambda: int(dut.ready) == 1)
            ready_time.append(sim.time)

        sim.run()
        self.assertGreater(ready_time[0], 0)
        self.assertEqual(int(dut.ready), 1)

    def test_timeout_fires(self):
        dut = Handshake()
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        timed_out = []

        @sim.initial
        def stim():
            dut.enable.set(0)  # never reaches 5
            try:
                yield until(lambda: int(dut.ready) == 1, timeout=100)
            except TimeoutError:
                timed_out.append(sim.time)

        sim.run()
        self.assertEqual(timed_out, [100])

    def test_condition_true_immediately(self):
        dut = Handshake()
        dut.cnt._val = 10  # already past threshold
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        resumed = []

        @sim.initial
        def stim():
            dut.enable.set(0)
            yield 1  # let comb settle
            yield until(lambda: int(dut.ready) == 1)
            resumed.append(sim.time)

        sim.run()
        self.assertEqual(len(resumed), 1)

    def test_deadlock_detected(self):
        dut = Handshake()
        sim = SimEngine(dut)
        # No clock, no timeout — deadlock

        @sim.initial
        def stim():
            yield until(lambda: int(dut.ready) == 1)

        with self.assertRaises(RuntimeError):
            sim.run()

    def test_yield_after_until(self):
        """Generator can yield normal delays after an until()."""
        dut = Handshake()
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        events = []

        @sim.initial
        def stim():
            dut.enable.set(1)
            yield until(lambda: int(dut.ready) == 1)
            events.append(('ready', sim.time))
            yield 20
            events.append(('after', sim.time))

        sim.run()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1][0], 'after')
        self.assertEqual(events[1][1], events[0][1] + 20)


class TestFork(unittest.TestCase):
    """Test fork() and fork_any()."""

    def _make_dut(self):
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
        return Counter()

    def test_fork_join_all(self):
        dut = self._make_dut()
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        events = []

        @sim.initial
        def stim():
            def wait3():
                yield until(lambda: int(dut.cnt) >= 3)
                events.append('a')
            def wait5():
                yield until(lambda: int(dut.cnt) >= 5)
                events.append('b')
            yield sim.fork(wait3, wait5)
            events.append('done')

        sim.run()
        self.assertEqual(events, ['a', 'b', 'done'])

    def test_fork_any(self):
        dut = self._make_dut()
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        events = []

        @sim.initial
        def stim():
            def fast():
                yield until(lambda: int(dut.cnt) >= 2)
                events.append('fast')
            def slow():
                yield until(lambda: int(dut.cnt) >= 100)
                events.append('slow')
            yield sim.fork_any(fast, slow)
            events.append('done')

        sim.run()
        self.assertEqual(events, ['fast', 'done'])

    def test_fork_with_delays(self):
        """Forked blocks can use normal yield delays."""
        dut = self._make_dut()
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        events = []

        @sim.initial
        def stim():
            def block_a():
                yield 30
                events.append(('a', sim.time))
            def block_b():
                yield 50
                events.append(('b', sim.time))
            yield sim.fork(block_a, block_b)
            events.append(('done', sim.time))

        sim.run()
        self.assertEqual(events[0], ('a', 30))
        self.assertEqual(events[1], ('b', 50))
        self.assertEqual(events[2][0], 'done')


class TestUntilVeripyTestCase(VeripyTestCase):
    """Test until() inside dual-path framework (iverilog path skipped)."""

    def create_module(self):
        return Handshake()

    def test_reactive_wait(self):
        @self.always
        def clock():
            self.set(clock=0); yield 5
            self.set(clock=1); yield 5

        @self.initial
        def stim():
            self.set(enable=1)
            yield until(lambda: self.out('ready') == 1)
            self.assertEqual(self.out('ready'), 1)

        self.run_sim()


if __name__ == '__main__':
    unittest.main()
