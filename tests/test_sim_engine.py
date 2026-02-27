"""Tests for SimEngine: event-driven simulation with Verilog scheduling semantics."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, posedge
from veripy.sim import SimEngine


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


class TestSimEngineInitial(unittest.TestCase):
    """Test @sim.initial blocks (run once)."""

    def test_initial_runs_and_stops(self):
        c = Counter()
        sim = SimEngine(c)
        ran = []

        @sim.initial
        def stim():
            ran.append(True)
            yield 1

        sim.run()
        self.assertEqual(len(ran), 1)

    def test_counter_via_sim_engine(self):
        c = Counter(n=4)
        sim = SimEngine(c)

        @sim.initial
        def stim():
            c.reset._val = 1
            c.enable._val = 1
            c.clock._val = 1
            yield 1
            c.reset._val = 0
            for _ in range(5):
                c.clock._val = 0
                yield 1
                c.clock._val = 1
                yield 1

        sim.run()
        self.assertEqual(int(c.count), 5)

    def test_multiple_initial_blocks(self):
        c = Counter(n=4)
        sim = SimEngine(c)
        order = []

        @sim.initial
        def block_a():
            order.append('a_start')
            yield 1
            order.append('a_end')

        @sim.initial
        def block_b():
            order.append('b_start')
            yield 1
            order.append('b_end')

        sim.run()
        self.assertIn('a_start', order)
        self.assertIn('b_start', order)
        self.assertIn('a_end', order)
        self.assertIn('b_end', order)

    def test_time_advances(self):
        c = Counter()
        sim = SimEngine(c)

        @sim.initial
        def stim():
            yield 5
            yield 3

        sim.run()
        self.assertEqual(sim.time, 8)


class TestSimEngineAlways(unittest.TestCase):
    """Test @sim.always blocks (restart on completion)."""

    def test_always_restarts(self):
        c = Counter()
        sim = SimEngine(c)
        ticks = []

        @sim.always
        def clock_gen():
            c.clock._val = 0
            yield 1
            c.clock._val = 1
            yield 1
            ticks.append(sim.time)

        @sim.initial
        def stop():
            yield 10
            sim.finish()

        sim.run()
        self.assertGreater(len(ticks), 1)


class TestSimEngineFinish(unittest.TestCase):
    def test_finish_stops_simulation(self):
        c = Counter()
        sim = SimEngine(c)
        count = []

        @sim.initial
        def stim():
            for _ in range(100):
                count.append(1)
                yield 1
                if len(count) >= 3:
                    sim.finish()

        sim.run()
        self.assertTrue(sim._finished)
        self.assertLessEqual(len(count), 4)


class TestSimEngineEdgeDetection(unittest.TestCase):
    def test_posedge_triggers_always_block(self):
        c = Counter(n=4)
        sim = SimEngine(c)

        @sim.initial
        def stim():
            c.enable._val = 1
            c.reset._val = 1
            c.clock._val = 1
            yield 1
            c.reset._val = 0
            for _ in range(3):
                c.clock._val = 0
                yield 1
                c.clock._val = 1
                yield 1

        sim.run()
        self.assertEqual(int(c.count), 3)


class TestVCDWaveformDump(unittest.TestCase):
    def test_vcd_file_created(self):
        import tempfile, os
        c = Counter(n=4)
        path = os.path.join(tempfile.mkdtemp(), 'test.vcd')
        sim = SimEngine(c, vcd=path)

        @sim.initial
        def stim():
            c.enable._val = 1
            c.clock._val = 1
            yield 1

        sim.run()
        self.assertTrue(os.path.exists(path))
        content = open(path).read()
        self.assertIn('$timescale', content)
        self.assertIn('$enddefinitions', content)
        self.assertIn('$dumpvars', content)

    def test_vcd_contains_signal_declarations(self):
        import tempfile, os
        c = Counter(n=4)
        path = os.path.join(tempfile.mkdtemp(), 'test.vcd')
        sim = SimEngine(c, vcd=path)

        @sim.initial
        def stim():
            yield 1

        sim.run()
        content = open(path).read()
        self.assertIn('clock', content)
        self.assertIn('count', content)
        self.assertIn('enable', content)
        self.assertIn('reset', content)

    def test_vcd_records_changes(self):
        import tempfile, os
        c = Counter(n=4)
        path = os.path.join(tempfile.mkdtemp(), 'test.vcd')
        sim = SimEngine(c, vcd=path)

        @sim.initial
        def stim():
            c.enable._val = 1
            c.reset._val = 1
            c.clock._val = 1
            yield 1
            c.reset._val = 0
            c.clock._val = 0
            yield 1
            c.clock._val = 1
            yield 1

        sim.run()
        content = open(path).read()
        # Should have timestep markers
        self.assertIn('#0', content)
        self.assertIn('#1', content)
        self.assertIn('#2', content)

    def test_vcd_no_file_without_option(self):
        """SimEngine without vcd= should not create any file."""
        c = Counter(n=4)
        sim = SimEngine(c)

        @sim.initial
        def stim():
            yield 1

        sim.run()
        self.assertIsNone(sim._vcd)


if __name__ == '__main__':
    unittest.main()
