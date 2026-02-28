"""Tests for @self.fsm decorator: FSM sugar."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Signal
from veripy.sim import SimEngine

T = 10


class Controller(Module):
    def __init__(self):
        self.clock = Input()
        self.reset = Input()
        self.start = Input()
        self.done  = Output()
        super().__init__()

        @self.fsm(self.clock, self.reset, states=['IDLE', 'LOAD', 'EXEC', 'DONE'])
        def ctrl(state):
            self.done = 0
            if state == IDLE:
                if self.start:
                    return LOAD
            elif state == LOAD:
                return EXEC
            elif state == EXEC:
                return DONE
            elif state == DONE:
                self.done = 1
                return IDLE


class TestFSMSimulation(unittest.TestCase):
    def test_starts_in_idle(self):
        c = Controller()
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.reset.set(1); yield T; c.reset.set(0)
            self.assertEqual(int(c._fsm_state), 0)
        sim.run()

    def test_stays_idle_without_start(self):
        c = Controller()
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.reset.set(1); yield T; c.reset.set(0)
            yield T
            self.assertEqual(int(c._fsm_state), 0)
        sim.run()

    def test_transitions_on_start(self):
        c = Controller()
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.reset.set(1); yield T; c.reset.set(0)
            c.start.set(1); yield T
            self.assertEqual(int(c._fsm_state), 1)  # LOAD
        sim.run()

    def test_full_cycle(self):
        c = Controller()
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.reset.set(1); yield T; c.reset.set(0)
            c.start.set(1); yield T  # → LOAD
            c.start.set(0); yield T  # → EXEC
            yield T                  # → DONE
            self.assertEqual(int(c.done), 1)
            yield T                  # → IDLE
            self.assertEqual(int(c._fsm_state), 0)
            self.assertEqual(int(c.done), 0)
        sim.run()

    def test_reset_returns_to_idle(self):
        c = Controller()
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.reset.set(1); yield T; c.reset.set(0)
            c.start.set(1); yield T  # → LOAD
            yield T                  # → EXEC
            c.reset.set(1); yield T  # → IDLE (reset)
            self.assertEqual(int(c._fsm_state), 0)
        sim.run()


class TestFSMSignals(unittest.TestCase):
    def test_state_register_created(self):
        c = Controller()
        self.assertIsInstance(c._fsm_state, Signal)
        self.assertEqual(c._fsm_state._kind, 'reg')

    def test_state_width(self):
        c = Controller()
        self.assertEqual(c._fsm_state.width, 2)


if __name__ == '__main__':
    unittest.main()
