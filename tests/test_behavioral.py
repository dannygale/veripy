"""Tests for behavioral emulation mode."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.sim import SimEngine, BehavioralSim

T = 10


class TestBehavioral(unittest.TestCase):
    def _make_module(self):
        class Accum(Module):
            """Accumulator: sim path uses register, behavioral is instant."""
            def __init__(self):
                self.clk   = Input()
                self.reset = Input()
                self.d     = Input(8)
                self.q     = Output(8)
                self.reg   = Register(8)
                super().__init__()

                @self.posedge(self.clk)
                def seq():
                    if self.reset:
                        self.reg = 0
                    else:
                        self.reg = int(self.reg) + int(self.d)

                @self.comb
                def out():
                    self.q = self.reg

                @self.behavioral
                def fast():
                    self.q._val = int(self.q) + int(self.d)

        return Accum()

    def test_sim_mode(self):
        m = self._make_module()
        sim = SimEngine(m)
        sim.clock(m.clk, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.d.set(10)
            yield T
            self.assertEqual(int(m.q), 10)
            yield T
            self.assertEqual(int(m.q), 20)
        sim.run()

    def test_behavioral_mode(self):
        m = self._make_module()
        bsim = BehavioralSim(m)
        m.d.set(10)
        bsim.step()
        self.assertEqual(int(m.q), 10)
        bsim.step()
        self.assertEqual(int(m.q), 20)

    def test_no_behavioral_uses_sim(self):
        """Module without @behavioral falls back to cycle-accurate."""
        class Simple(Module):
            def __init__(self):
                self.d = Input(8)
                self.q = Output(8)
                super().__init__()

                @self.comb
                def drive():
                    self.q = self.d

        m = Simple()
        bsim = BehavioralSim(m)
        m.d.set(42)
        bsim.step()
        self.assertEqual(int(m.q), 42)


if __name__ == '__main__':
    unittest.main()
