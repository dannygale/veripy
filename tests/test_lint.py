"""Tests for veripy lint: static checks on modules."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, Signal
from veripy.lint import lint


class TestUndrivenOutput(unittest.TestCase):
    def test_detects_undriven(self):
        class M(Module):
            def __init__(self):
                self.q = Output(8)
                super().__init__()

        msgs = [m for _, m in lint(M())]
        self.assertTrue(any("'q' is never driven" in m for m in msgs))

    def test_no_warning_when_driven(self):
        class M(Module):
            def __init__(self):
                self.d = Input(8)
                self.q = Output(8)
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.d

        self.assertEqual(lint(M()), [])


class TestMultiDriven(unittest.TestCase):
    def test_detects_multi_driven(self):
        class M(Module):
            def __init__(self):
                self.a = Input(8)
                self.b = Input(8)
                self.q = Output(8)
                super().__init__()
                @self.comb
                def block1():
                    self.q = self.a
                @self.comb
                def block2():
                    self.q = self.b

        msgs = [m for _, m in lint(M())]
        self.assertTrue(any('multiple blocks' in m for m in msgs))


class TestMissingReset(unittest.TestCase):
    def test_detects_missing_reset(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.d = Input(8)
                self.r = Register(8)
                self.q = Output(8)
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.r
                @self.posedge(self.clk)
                def logic():
                    self.r = self.d

        msgs = [m for _, m in lint(M())]
        self.assertTrue(any('no reset path' in m for m in msgs))

    def test_no_warning_with_reset(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.reset = Input()
                self.d = Input(8)
                self.r = Register(8)
                self.q = Output(8)
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.r
                @self.posedge(self.clk)
                def logic():
                    if self.reset:
                        self.r = 0
                    else:
                        self.r = self.d

        warnings = [w for w in lint(M()) if 'reset' in w[1]]
        self.assertEqual(warnings, [])


class TestUnusedSignal(unittest.TestCase):
    def test_detects_unused(self):
        class M(Module):
            def __init__(self):
                self.d = Input(8)
                self.q = Output(8)
                self.unused = Register(8)
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.d

        msgs = [m for _, m in lint(M())]
        self.assertTrue(any("'unused'" in m and 'never read' in m for m in msgs))


class TestCleanModule(unittest.TestCase):
    def test_counter_is_clean(self):
        from examples.counter import Counter
        self.assertEqual(lint(Counter(n=4)), [])

    def test_cdc_violation(self):
        """Detect register read across clock domains."""
        class CDCBad(Module):
            def __init__(self):
                self.fast_clk = Input()
                self.slow_clk = Input()
                self.d        = Input(8)
                self.q        = Output(8)
                self.reg_fast = Register(8)
                self.reg_slow = Register(8)
                super().__init__()

                @self.posedge(self.fast_clk)
                def fast_domain():
                    self.reg_fast = self.d

                @self.posedge(self.slow_clk)
                def slow_domain():
                    self.reg_slow = self.reg_fast  # CDC!

                @self.comb
                def out():
                    self.q = self.reg_slow

        w = lint(CDCBad())
        cdc = [m for lvl, m in w if 'CDC' in m]
        self.assertEqual(len(cdc), 1)
        self.assertIn('reg_fast', cdc[0])
        self.assertIn('fast_clk', cdc[0])
        self.assertIn('slow_clk', cdc[0])

    def test_no_cdc_single_clock(self):
        """No CDC warning when only one clock domain exists."""
        class SingleClock(Module):
            def __init__(self):
                self.clk = Input()
                self.a   = Register(8)
                self.b   = Register(8)
                super().__init__()

                @self.posedge(self.clk)
                def block1():
                    self.a = self.b

                @self.posedge(self.clk)
                def block2():
                    self.b = self.a

        w = lint(SingleClock())
        cdc = [m for lvl, m in w if 'CDC' in m]
        self.assertEqual(len(cdc), 0)


if __name__ == '__main__':
    unittest.main()
