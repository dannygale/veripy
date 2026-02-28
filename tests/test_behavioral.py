"""Tests for behavioral emulation mode."""
import unittest
from veripy import Module, Input, Output, Register


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
        m.reset.set(1); m.tick(); m.reset.set(0)
        m.d.set(10)
        m.tick()
        self.assertEqual(int(m.q), 10)
        m.tick()
        self.assertEqual(int(m.q), 20)

    def test_behavioral_mode(self):
        m = self._make_module()
        m._mode = 'behavioral'
        m.d.set(10)
        m.tick()
        self.assertEqual(int(m.q), 10)
        m.tick()
        self.assertEqual(int(m.q), 20)

    def test_mode_switch(self):
        m = self._make_module()
        m._mode = 'behavioral'
        m.d.set(5)
        m.tick()
        self.assertEqual(int(m.q), 5)
        # Switch to sim — register starts from 0, not behavioral state
        m._mode = 'sim'
        m.reset.set(1); m.tick(); m.reset.set(0)
        m.d.set(3)
        m.tick()
        self.assertEqual(int(m.q), 3)

    def test_submodule_behavioral(self):
        accum = self._make_module()

        class Top(Module):
            def __init__(self):
                self.d   = Input(8)
                self.out = Output(8)
                self.acc = accum
                super().__init__()

                @self.comb
                def wire():
                    self.acc.d = self.d
                    self.out = self.acc.q

        top = Top()
        top.acc._mode = 'behavioral'
        top.d.set(7)
        top.tick()
        self.assertEqual(int(top.out), 7)

    def test_no_behavioral_uses_sim(self):
        """Module without @behavioral in behavioral mode falls through to sim."""
        class Simple(Module):
            def __init__(self):
                self.d = Input(8)
                self.q = Output(8)
                super().__init__()

                @self.comb
                def drive():
                    self.q = self.d

        m = Simple()
        m._mode = 'behavioral'
        m.d.set(42)
        m.tick()
        self.assertEqual(int(m.q), 42)


if __name__ == '__main__':
    unittest.main()
