"""Tests for veripy lint: static checks on modules."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, Signal, module, posedge
from veripy.context import comb, always, clock_domain
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


class TestCombLoop(unittest.TestCase):
    def test_detects_simple_loop(self):
        """Two @comb blocks forming a cycle: a→b and b→a."""
        class M(Module):
            def __init__(self):
                self.a = Signal(8)
                self.b = Signal(8)
                super().__init__()

                @self.comb
                def f1():
                    self.b = self.a

                @self.comb
                def f2():
                    self.a = self.b

        m = M()
        results = lint(m)
        msgs = [msg for lvl, msg in results if 'combinational loop' in msg]
        self.assertEqual(len(msgs), 1)
        self.assertIn('a', msgs[0])
        self.assertIn('b', msgs[0])

    def test_no_loop_clean(self):
        """Linear chain: a→b→c should not trigger."""
        class M(Module):
            def __init__(self):
                self.inp = Input(8)
                self.mid = Signal(8)
                self.out = Output(8)
                super().__init__()

                @self.comb
                def f1():
                    self.mid = self.inp

                @self.comb
                def f2():
                    self.out = self.mid

        m = M()
        results = lint(m)
        msgs = [msg for lvl, msg in results if 'combinational loop' in msg]
        self.assertEqual(len(msgs), 0)

    def test_self_loop_excluded(self):
        """Reading and writing the same signal in one block is not a loop."""
        class M(Module):
            def __init__(self):
                self.inp = Input(8)
                self.out = Output(8)
                super().__init__()

                @self.comb
                def f():
                    self.out = self.inp

        m = M()
        results = lint(m)
        msgs = [msg for lvl, msg in results if 'combinational loop' in msg]
        self.assertEqual(len(msgs), 0)

    def test_three_signal_loop(self):
        """Three-signal cycle: a→b→c→a."""
        class M(Module):
            def __init__(self):
                self.a = Signal(8)
                self.b = Signal(8)
                self.c = Signal(8)
                super().__init__()

                @self.comb
                def f1():
                    self.b = self.a

                @self.comb
                def f2():
                    self.c = self.b

                @self.comb
                def f3():
                    self.a = self.c

        m = M()
        results = lint(m)
        msgs = [msg for lvl, msg in results if 'combinational loop' in msg]
        self.assertEqual(len(msgs), 1)


class TestClockDomain(unittest.TestCase):
    """Tests for explicit clock domain annotation API and enhanced CDC messages."""

    def test_cdc_with_domain_names(self):
        """CDC message uses domain names when clock_domain() is declared."""
        class M(Module):
            def __init__(self):
                self.fast_clk = Input()
                self.slow_clk = Input()
                self.d        = Input(8)
                self.q        = Output(8)
                self.reg_fast = Register(8)
                self.reg_slow = Register(8)
                super().__init__()

                self.clock_domain('fast', self.fast_clk)
                self.clock_domain('slow', self.slow_clk)

                @self.posedge(self.fast_clk)
                def fast_domain():
                    self.reg_fast = self.d

                @self.posedge(self.slow_clk)
                def slow_domain():
                    self.reg_slow = self.reg_fast

                @self.comb
                def out():
                    self.q = self.reg_slow

        w = lint(M())
        cdc = [m for lvl, m in w if 'CDC' in m]
        self.assertEqual(len(cdc), 1)
        self.assertIn("domain 'fast'", cdc[0])
        self.assertIn("domain 'slow'", cdc[0])
        self.assertIn('reg_fast', cdc[0])

    def test_cdc_falls_back_without_domains(self):
        """Without clock_domain(), CDC message uses clock signal names."""
        class M(Module):
            def __init__(self):
                self.clk_a = Input()
                self.clk_b = Input()
                self.r     = Register(8)
                self.s     = Register(8)
                self.q     = Output(8)
                super().__init__()

                @self.posedge(self.clk_a)
                def a():
                    self.r = 1

                @self.posedge(self.clk_b)
                def b():
                    self.s = self.r

                @self.comb
                def out():
                    self.q = self.s

        w = lint(M())
        cdc = [m for lvl, m in w if 'CDC' in m]
        self.assertEqual(len(cdc), 1)
        self.assertIn('clk_a', cdc[0])
        self.assertIn('clk_b', cdc[0])
        # Should NOT contain domain keyword
        self.assertNotIn("domain '", cdc[0])

    def test_decorator_module_clock_domain(self):
        """clock_domain() works with @module decorator."""
        @module
        def cdc_mod():
            fclk = Input()
            sclk = Input()
            d    = Input(8)
            q    = Output(8)
            rf   = Register(8)
            rs   = Register(8)

            clock_domain('fast', fclk)
            clock_domain('slow', sclk)

            @always(posedge(fclk))
            def fd():
                rf = d

            @always(posedge(sclk))
            def sd():
                rs = rf

            @comb
            def out():
                q = rs

        w = lint(cdc_mod())
        cdc = [m for lvl, m in w if 'CDC' in m]
        self.assertEqual(len(cdc), 1)
        self.assertIn("domain 'fast'", cdc[0])
        self.assertIn("domain 'slow'", cdc[0])

    def test_no_cdc_same_domain(self):
        """No CDC warning when both blocks use the same clock domain."""
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.a   = Register(8)
                self.b   = Register(8)
                super().__init__()

                self.clock_domain('sys', self.clk)

                @self.posedge(self.clk)
                def w1():
                    self.a = self.b

                @self.posedge(self.clk)
                def w2():
                    self.b = self.a

        cdc = [m for lvl, m in lint(M()) if 'CDC' in m]
        self.assertEqual(len(cdc), 0)


if __name__ == '__main__':
    unittest.main()
