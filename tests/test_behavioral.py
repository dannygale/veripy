"""Tests for behavioral emulation mode (@behavioral decorator)."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.verify import TestBench, initial

T = 10


class Accum(Module):
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


class TestBehavioralDecorator(TestBench):
    def create_module(self): return Accum()

    def test_sim_mode(self):
        dut = self.dut; self.clock('clk', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.d = 10; yield T
            self.assertEqual(self.get('q'), 10)
            yield T
            self.assertEqual(self.get('q'), 20)


class TestNoBehavioralFallback(unittest.TestCase):
    def test_comb_only_module(self):
        class Simple(Module):
            def __init__(self):
                self.d = Input(8)
                self.q = Output(8)
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.d

        m = Simple()
        m.d.set(42)
        m._settle_comb()
        self.assertEqual(int(m.q), 42)


if __name__ == '__main__':
    unittest.main()
