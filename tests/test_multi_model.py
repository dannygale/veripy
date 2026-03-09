"""Tests for multi-model dispatch in TestBench (_resolve_models, floor pinning, cross-check)."""
import os
import unittest

from veripy import Module, Input, Output, Register, TestBench
from veripy.verify import _resolve_models


# ---------------------------------------------------------------------------
# Minimal module fixtures
# ---------------------------------------------------------------------------

class _RTLOnly(Module):
    """Module with RTL only — no functional or cycle model."""
    def __init__(self):
        self.clk = Input()
        self.d   = Input(8)
        self.q   = Output(8)
        self.r   = Register(8)
        super().__init__()

        @self.comb
        def drive():
            self.q = self.r

        @self.posedge(self.clk)
        def seq():
            self.r = self.d


class _WithFunctional(Module):
    """Module with a @functional model and RTL."""
    def __init__(self):
        self.clk = Input()
        self.d   = Input(8)
        self.q   = Output(8)
        self.r   = Register(8)
        super().__init__()

        @self.comb
        def drive():
            self.q = self.r

        @self.posedge(self.clk)
        def seq():
            self.r = self.d

        @self.functional
        def fast():
            self.q._val = int(self.d)


class _WithCycle(Module):
    """Module with a @cycle model and RTL."""
    def __init__(self):
        self.clk = Input()
        self.d   = Input(8)
        self.q   = Output(8)
        self.r   = Register(8)
        super().__init__()

        @self.comb
        def drive():
            self.q = self.r

        @self.posedge(self.clk)
        def seq():
            self.r = self.d

        @self.cycle
        def cyc():
            self.r._val = int(self.d)
            self.q._val = int(self.r)


# ---------------------------------------------------------------------------
# _resolve_models unit tests
# ---------------------------------------------------------------------------

class TestResolveModels(unittest.TestCase):
    def setUp(self):
        # Ensure no env override bleeds in
        os.environ.pop('VERIPY_MODEL', None)

    def tearDown(self):
        os.environ.pop('VERIPY_MODEL', None)

    def test_rtl_only_returns_rtl(self):
        mod = _RTLOnly()
        result = _resolve_models(TestBench, mod)
        self.assertEqual(result, ['rtl'])

    def test_functional_module_returns_functional_and_rtl(self):
        mod = _WithFunctional()
        result = _resolve_models(TestBench, mod)
        self.assertIn('functional', result)
        self.assertIn('rtl', result)
        self.assertNotIn('cycle', result)

    def test_cycle_module_returns_cycle_and_rtl(self):
        mod = _WithCycle()
        result = _resolve_models(TestBench, mod)
        self.assertIn('cycle', result)
        self.assertIn('rtl', result)
        self.assertNotIn('functional', result)

    def test_floor_pinning_skips_lower_models(self):
        """model='cycle' floor should exclude functional."""
        mod = _WithFunctional()

        class Pinned(TestBench):
            model = 'cycle'

        result = _resolve_models(Pinned, mod)
        self.assertNotIn('functional', result)
        self.assertIn('rtl', result)

    def test_floor_rtl_returns_only_rtl(self):
        mod = _WithFunctional()

        class PinnedRTL(TestBench):
            model = 'rtl'

        result = _resolve_models(PinnedRTL, mod)
        self.assertEqual(result, ['rtl'])

    def test_env_override_raises_floor(self):
        """VERIPY_MODEL env var acts as floor, overriding class attribute."""
        mod = _WithFunctional()
        os.environ['VERIPY_MODEL'] = 'rtl'
        result = _resolve_models(TestBench, mod)
        self.assertEqual(result, ['rtl'])

    def test_env_override_beats_class_attribute(self):
        """Env var takes priority over class model attribute."""
        mod = _WithFunctional()

        class PinnedFunctional(TestBench):
            model = 'functional'

        os.environ['VERIPY_MODEL'] = 'rtl'
        result = _resolve_models(PinnedFunctional, mod)
        self.assertEqual(result, ['rtl'])

    def test_floor_below_available_returns_all_available(self):
        """Floor of 'functional' on a module that has functional+rtl → both."""
        mod = _WithFunctional()

        class PinnedFunctional(TestBench):
            model = 'functional'

        result = _resolve_models(PinnedFunctional, mod)
        self.assertIn('functional', result)
        self.assertIn('rtl', result)


# ---------------------------------------------------------------------------
# Integration: TestBench runs multi-model and cross-checks
# ---------------------------------------------------------------------------

class _PassThroughModule(Module):
    """Simple pass-through with a @functional model that agrees with RTL."""
    def __init__(self):
        self.clk = Input()
        self.d   = Input(8)
        self.q   = Output(8)
        self.r   = Register(8)
        super().__init__()

        @self.comb
        def drive():
            self.q = self.r

        @self.posedge(self.clk)
        def seq():
            self.r = self.d

        @self.functional
        def fast():
            # Agrees with RTL: output follows input with 1-cycle delay
            self.q._val = int(self.d)


class TestMultiModelDispatch(TestBench):
    """Runs against functional + rtl; cross-check should pass (models agree)."""
    backend = 'behavioral'  # Python RTL sim for RTL pass — no external tools needed

    def create_module(self):
        return _PassThroughModule()

    def test_passthrough(self):
        @self.run_testbench(clock='clk', period=10)
        def run():
            self.set(d=0xAB)
            yield 10
            self.out('q')
            self.set(d=0x12)
            yield 10
            self.out('q')


class TestCrossCheckDetectsDivergence(unittest.TestCase):
    """The cross-check should raise AssertionError when backends disagree."""

    def test_assert_all_match_fails_on_divergence(self):
        """_assert_all_match() fails when two backends report different values."""
        bench = TestBench.__new__(TestBench)
        bench._all_outputs = {
            'functional': {10: {'q': 0}},
            'behavioral': {10: {'q': 171}},  # 0xAB
        }
        with self.assertRaises(AssertionError):
            bench._assert_all_match()

    def test_assert_all_match_passes_when_equal(self):
        bench = TestBench.__new__(TestBench)
        bench._all_outputs = {
            'functional': {10: {'q': 171}},
            'behavioral': {10: {'q': 171}},
        }
        bench._assert_all_match()  # should not raise

    def test_assert_all_match_ignores_missing_points(self):
        """If one backend has no reading at a time, that point is skipped."""
        bench = TestBench.__new__(TestBench)
        bench._all_outputs = {
            'functional': {10: {'q': 5}},
            'behavioral': {20: {'q': 7}},  # different time — no overlap
        }
        bench._assert_all_match()  # should not raise


if __name__ == '__main__':
    unittest.main()
