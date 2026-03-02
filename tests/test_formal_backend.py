"""Tests for formal verification backend: IR lowering, Verilog emission, .sby generation."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, module, posedge
from veripy.context import comb, always, assert_always, cover, assume
from veripy.lower import lower_module
from veripy.backend_verilog import emit_verilog
from veripy.backend_formal import emit_sby
from veripy.ir import FormalProperty


class Counter(Module):
    def __init__(self, n=4):
        self.clock = Input()
        self.reset = Input()
        self.enable = Input()
        self.count = Output(n)
        self.cnt = Register(n)
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

        @self.assert_always(self.clock)
        def bounded():
            return self.count < 16

        @self.cover(self.clock)
        def reaches_five():
            return self.count == 5

        @self.assume(self.clock)
        def valid_input():
            return self.enable | self.reset


# ── IR lowering tests ────────────────────────────────────────────────

class TestFormalIR(unittest.TestCase):
    def test_assert_lowered(self):
        ir = lower_module(Counter(), 'counter')
        asserts = [p for p in ir.formal_props if p.kind == 'assert']
        self.assertEqual(len(asserts), 1)
        self.assertEqual(asserts[0].name, 'bounded')
        self.assertEqual(asserts[0].clock, 'clock')
        self.assertEqual(asserts[0].edge, 'posedge')

    def test_cover_lowered(self):
        ir = lower_module(Counter(), 'counter')
        covers = [p for p in ir.formal_props if p.kind == 'cover']
        self.assertEqual(len(covers), 1)
        self.assertEqual(covers[0].name, 'reaches_five')

    def test_assume_lowered(self):
        ir = lower_module(Counter(), 'counter')
        assumes = [p for p in ir.formal_props if p.kind == 'assume']
        self.assertEqual(len(assumes), 1)
        self.assertEqual(assumes[0].name, 'valid_input')

    def test_no_formal_props(self):
        class Plain(Module):
            def __init__(self):
                self.d = Input()
                self.q = Output()
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.d
        ir = lower_module(Plain(), 'plain')
        self.assertEqual(ir.formal_props, [])


# ── Verilog emission tests ───────────────────────────────────────────

class TestFormalVerilog(unittest.TestCase):
    def test_ifdef_formal_block(self):
        v = Counter().to_verilog()
        self.assertIn('`ifdef FORMAL', v)
        self.assertIn('`endif', v)

    def test_assert_emitted(self):
        v = Counter().to_verilog()
        self.assertIn('assert(', v)
        self.assertIn('// bounded', v)

    def test_cover_emitted(self):
        v = Counter().to_verilog()
        self.assertIn('cover(', v)
        self.assertIn('// reaches_five', v)

    def test_assume_emitted(self):
        v = Counter().to_verilog()
        self.assertIn('assume(', v)
        self.assertIn('// valid_input', v)

    def test_clock_sensitivity(self):
        v = Counter().to_verilog()
        # Formal properties should be in always @(posedge clock) blocks
        lines = v.split('\n')
        in_formal = False
        for line in lines:
            if '`ifdef FORMAL' in line:
                in_formal = True
            if in_formal and 'assert(' in line:
                # Find the preceding always block
                break
        self.assertIn('always @(posedge clock)', v.split('`ifdef FORMAL')[1])

    def test_no_formal_when_empty(self):
        class Plain(Module):
            def __init__(self):
                self.d = Input()
                self.q = Output()
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.d
        v = Plain().to_verilog()
        self.assertNotIn('`ifdef FORMAL', v)


# ── .sby generation tests ────────────────────────────────────────────

class TestSbyGeneration(unittest.TestCase):
    def test_sby_has_tasks(self):
        ir = lower_module(Counter(), 'counter')
        sby = emit_sby(ir)
        self.assertIn('[tasks]', sby)
        self.assertIn('bmc', sby)
        self.assertIn('cover', sby)

    def test_sby_options(self):
        ir = lower_module(Counter(), 'counter')
        sby = emit_sby(ir)
        self.assertIn('bmc: mode bmc', sby)
        self.assertIn('bmc: depth 20', sby)
        self.assertIn('cover: mode cover', sby)

    def test_sby_custom_depth(self):
        ir = lower_module(Counter(), 'counter')
        sby = emit_sby(ir, depth=50)
        self.assertIn('depth 50', sby)

    def test_sby_engine(self):
        ir = lower_module(Counter(), 'counter')
        sby = emit_sby(ir)
        self.assertIn('[engines]', sby)
        self.assertIn('smtbmc', sby)

    def test_sby_script(self):
        ir = lower_module(Counter(), 'counter')
        sby = emit_sby(ir)
        self.assertIn('read_verilog -formal counter.v', sby)
        self.assertIn('prep -top counter', sby)

    def test_sby_files(self):
        ir = lower_module(Counter(), 'counter')
        sby = emit_sby(ir)
        self.assertIn('[files]', sby)
        self.assertIn('counter.v', sby)

    def test_sby_assert_only(self):
        class AssertOnly(Module):
            def __init__(self):
                self.clk = Input()
                self.d = Input()
                self.q = Output()
                super().__init__()
                @self.comb
                def drive():
                    self.q = self.d
                @self.assert_always(self.clk)
                def check():
                    return self.q == self.d
        ir = lower_module(AssertOnly(), 'assert_only')
        sby = emit_sby(ir)
        self.assertIn('bmc', sby)
        self.assertNotIn('cover:', sby)

    def test_sby_cover_only(self):
        class CoverOnly(Module):
            def __init__(self):
                self.clk = Input()
                self.d = Input(4)
                super().__init__()
                @self.cover(self.clk)
                def hit_max():
                    return self.d == 15
        ir = lower_module(CoverOnly(), 'cover_only')
        sby = emit_sby(ir)
        self.assertIn('cover', sby)
        self.assertNotIn('bmc:', sby)


# ── @module decorator API tests ──────────────────────────────────────

class TestFormalModuleDecorator(unittest.TestCase):
    def test_module_decorator_formal(self):
        @module
        def my_counter(n=4):
            clock = Input()
            reset = Input()
            count = Output(n)
            cnt = Register(n)

            @comb
            def drive():
                count = cnt

            @always(posedge(clock))
            def inc():
                if reset:
                    cnt = 0
                else:
                    cnt = cnt + 1

            @assert_always(clock)
            def bounded():
                return int(count) < 16

            @cover(clock)
            def reaches_max():
                return int(count) == 15

            @assume(clock)
            def reset_or_run():
                return True

        c = my_counter()
        v = c.to_verilog()
        self.assertIn('`ifdef FORMAL', v)
        self.assertIn('assert(', v)
        self.assertIn('cover(', v)
        self.assertIn('assume(', v)

    def test_module_decorator_ir(self):
        @module
        def simple():
            clk = Input()
            d = Input()
            q = Output()

            @comb
            def drive():
                q = d

            @assert_always(clk)
            def pass_through():
                return int(q) == int(d)

        ir = lower_module(simple(), 'simple')
        self.assertEqual(len(ir.formal_props), 1)
        self.assertEqual(ir.formal_props[0].kind, 'assert')
        self.assertEqual(ir.formal_props[0].name, 'pass_through')


if __name__ == '__main__':
    unittest.main()
