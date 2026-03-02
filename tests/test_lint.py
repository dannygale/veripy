"""Tests for veripy lint: static checks on modules."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, Signal, module, posedge
from veripy.context import comb, always
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


class TestModuleDecoratorLint(unittest.TestCase):
    """Lint must work on @module-decorated modules (uses _veripy_emit_source)."""

    def test_decorator_module_clean(self):
        @module
        def clean():
            d   = Input(8)
            out = Output(8)

            @comb
            def drive():
                out = d

        self.assertEqual(lint(clean()), [])

    def test_decorator_module_detects_undriven(self):
        @module
        def undriven():
            d   = Input(8)
            out = Output(8)

        msgs = [m for _, m in lint(undriven())]
        self.assertTrue(any("'out' is never driven" in m for m in msgs))


class TestLatchInference(unittest.TestCase):
    """Check 6: detect signals not assigned on all paths in @comb blocks."""

    def test_missing_else_warns(self):
        class M(Module):
            def __init__(self):
                self.sel = Input()
                self.a   = Input(8)
                self.out = Output(8)
                super().__init__()

                @self.comb
                def logic():
                    if self.sel:
                        self.out = self.a

        msgs = [m for _, m in lint(M())]
        self.assertTrue(any('latch inferred' in m and "'out'" in m for m in msgs))

    def test_complete_if_else_clean(self):
        class M(Module):
            def __init__(self):
                self.sel = Input()
                self.a   = Input(8)
                self.b   = Input(8)
                self.out = Output(8)
                super().__init__()

                @self.comb
                def logic():
                    if self.sel:
                        self.out = self.a
                    else:
                        self.out = self.b

        latch_warns = [m for _, m in lint(M()) if 'latch' in m]
        self.assertEqual(latch_warns, [])

    def test_elif_without_else_warns(self):
        class M(Module):
            def __init__(self):
                self.sel = Input(2)
                self.a   = Input(8)
                self.b   = Input(8)
                self.out = Output(8)
                super().__init__()

                @self.comb
                def logic():
                    if self.sel == 0:
                        self.out = self.a
                    elif self.sel == 1:
                        self.out = self.b

        msgs = [m for _, m in lint(M())]
        self.assertTrue(any('latch inferred' in m for m in msgs))

    def test_unconditional_assign_no_latch(self):
        class M(Module):
            def __init__(self):
                self.sel = Input()
                self.a   = Input(8)
                self.x   = Output(8)
                self.y   = Output(8)
                super().__init__()

                @self.comb
                def logic():
                    self.x = self.a
                    if self.sel:
                        self.y = self.a

        msgs = [m for _, m in lint(M())]
        latch = [m for m in msgs if 'latch' in m]
        # x is always assigned → no latch; y is conditional → latch
        self.assertFalse(any("'x'" in m for m in latch))
        self.assertTrue(any("'y'" in m for m in latch))

    def test_decorator_module_latch(self):
        """Latch detection works on @module-decorated modules too."""
        @module
        def latchy():
            sel = Input()
            a   = Input(8)
            out = Output(8)

            @comb
            def logic():
                if sel:
                    out = a

        msgs = [m for _, m in lint(latchy())]
        self.assertTrue(any('latch inferred' in m for m in msgs))


if __name__ == '__main__':
    unittest.main()
