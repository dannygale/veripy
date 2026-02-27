"""Tests for @self.fsm decorator: FSM sugar."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Signal


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
    def setUp(self):
        self.c = Controller()
        self.c.reset.set(1)
        self.c.tick()
        self.c.reset.set(0)

    def test_starts_in_idle(self):
        self.assertEqual(int(self.c._fsm_state), 0)

    def test_stays_idle_without_start(self):
        self.c.tick()
        self.assertEqual(int(self.c._fsm_state), 0)

    def test_transitions_on_start(self):
        self.c.start.set(1)
        self.c.tick()
        self.assertEqual(int(self.c._fsm_state), 1)  # LOAD

    def test_full_cycle(self):
        self.c.start.set(1)
        self.c.tick()  # → LOAD
        self.c.start.set(0)
        self.c.tick()  # → EXEC
        self.c.tick()  # → DONE
        self.assertEqual(int(self.c.done), 1)
        self.c.tick()  # → IDLE
        self.assertEqual(int(self.c._fsm_state), 0)
        self.assertEqual(int(self.c.done), 0)

    def test_reset_returns_to_idle(self):
        self.c.start.set(1)
        self.c.tick()  # → LOAD
        self.c.tick()  # → EXEC
        self.c.reset.set(1)
        self.c.tick()  # → IDLE (reset)
        self.assertEqual(int(self.c._fsm_state), 0)


class TestFSMSignals(unittest.TestCase):
    def test_state_register_created(self):
        c = Controller()
        self.assertIsInstance(c._fsm_state, Signal)
        self.assertEqual(c._fsm_state._kind, 'reg')

    def test_state_width(self):
        c = Controller()
        # 4 states → 2 bits
        self.assertEqual(c._fsm_state.width, 2)


if __name__ == '__main__':
    unittest.main()
