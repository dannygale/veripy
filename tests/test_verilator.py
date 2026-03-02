"""Tests for the Verilator co-simulation backend."""

import os
import shutil
import unittest

from veripy import Module, Input, Output, Register
from veripy.backend_verilator import (
    _generate_wrapper, _has_verilator, compile_verilator, compile_module,
    VerilatorModel,
)

HAS_VERILATOR = _has_verilator()
skip_no_verilator = unittest.skipUnless(HAS_VERILATOR, 'verilator not installed')


class TestGenerateWrapper(unittest.TestCase):
    """Test C++ wrapper code generation (no verilator needed)."""

    def test_basic_wrapper(self):
        signals = {'clock': ('input', 1), 'count': ('output', 8)}
        code = _generate_wrapper('counter', signals)
        self.assertIn('#include "Vcounter.h"', code)
        self.assertIn('veripy_create', code)
        self.assertIn('veripy_destroy', code)
        self.assertIn('veripy_eval', code)
        self.assertIn('veripy_set_clock', code)
        self.assertIn('veripy_get_clock', code)
        self.assertIn('veripy_get_count', code)

    def test_no_setter_for_output(self):
        signals = {'out': ('output', 16)}
        code = _generate_wrapper('dut', signals)
        self.assertNotIn('veripy_set_out', code)
        self.assertIn('veripy_get_out', code)

    def test_width_types(self):
        signals = {
            'a': ('input', 1),
            'b': ('input', 16),
            'c': ('input', 32),
            'd': ('input', 64),
        }
        code = _generate_wrapper('dut', signals)
        self.assertIn('uint8_t', code)
        self.assertIn('uint16_t', code)
        self.assertIn('uint32_t', code)
        self.assertIn('uint64_t', code)

    def test_extern_c_block(self):
        code = _generate_wrapper('m', {'x': ('input', 1)})
        self.assertIn('extern "C"', code)
        self.assertIn('}  // extern "C"', code)


class TestHasVerilator(unittest.TestCase):
    def test_returns_bool(self):
        result = _has_verilator()
        self.assertIsInstance(result, bool)


class TestCompileVerilatorNoTool(unittest.TestCase):
    @unittest.skipIf(HAS_VERILATOR, 'verilator IS installed')
    def test_raises_without_verilator(self):
        with self.assertRaises(RuntimeError) as ctx:
            compile_verilator('module m; endmodule', 'm', {})
        self.assertIn('verilator not found', str(ctx.exception))


class Counter(Module):
    def __init__(self, width=4):
        self.clock = Input()
        self.reset = Input()
        self.enable = Input()
        self.count = Output(width)
        self.cnt = Register(width)
        super().__init__()

        @self.comb
        def drive():
            self.count = self.cnt

        @self.posedge(self.clock)
        def inc():
            if self.reset:
                self.cnt = 0
            elif self.enable:
                self.cnt = self.cnt + 1


@skip_no_verilator
class TestCompileModule(unittest.TestCase):
    def test_compile_counter(self):
        mod = Counter(4)
        with compile_module(mod) as vm:
            # Reset
            vm.set('reset', 1)
            vm.set('enable', 1)
            vm.set('clock', 0)
            vm.eval()
            vm.set('clock', 1)
            vm.eval()
            self.assertEqual(vm.get('count'), 0)

            # Count
            vm.set('reset', 0)
            for i in range(5):
                vm.set('clock', 0)
                vm.eval()
                vm.set('clock', 1)
                vm.eval()
            self.assertEqual(vm.get('count'), 5)

    def test_step_helper(self):
        mod = Counter(4)
        with compile_module(mod) as vm:
            vm.set('reset', 1)
            vm.set('enable', 1)
            vm.step('clock')
            self.assertEqual(vm.get('count'), 0)
            vm.set('reset', 0)
            vm.step('clock', 3)
            self.assertEqual(vm.get('count'), 3)

    def test_context_manager_cleanup(self):
        mod = Counter(4)
        with compile_module(mod) as vm:
            tmpdir = vm._tmpdir
            self.assertTrue(os.path.isdir(tmpdir))
        self.assertFalse(os.path.isdir(tmpdir))


@skip_no_verilator
class TestVeripyTestCaseVerilator(unittest.TestCase):
    """Test that VeripyTestCase._run_verilator works."""

    def test_run_verilator_integration(self):
        from veripy import VeripyTestCase

        class TC(VeripyTestCase):
            USE_VERILATOR = True
            def create_module(self):
                return Counter(4)

        tc = TC('test_run_verilator_integration')
        tc._begin()
        tc.set(reset=1, enable=1, clock=0)
        tc.out('count')
        tc.set(clock=1)
        tc.out('count')
        tc.run_sim()
        ok = tc._run_verilator()
        self.assertTrue(ok)


if __name__ == '__main__':
    unittest.main()
