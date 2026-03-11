"""Tests for the @module decorator API."""
import unittest

from veripy.signal import Input, Output, Register, Mem
from veripy.context import comb, always
from veripy import posedge, negedge
from veripy.decorator import module
from veripy.parameter import Parameter
from veripy.verify import TestBench, initial

T = 10


# --- Module definitions ---

@module
def counter(width=8):
    clock  = Input()
    reset  = Input()
    enable = Input()
    count  = Output(width)
    cnt    = Register(width)

    @comb
    def drive():
        count = cnt

    @always(posedge(clock))
    def increment():
        if reset:
            cnt = 0
        elif enable:
            cnt += 1


@module
def simple_alu(width=8):
    a   = Input(width)
    b   = Input(width)
    op  = Input()
    out = Output(width)

    @comb
    def compute():
        if op:
            out = a - b
        else:
            out = a + b


@module
def datapath(width=16):
    clock  = Input()
    a      = Input(width)
    b      = Input(width)
    result = Output(width)
    alu    = simple_alu(width=width)

    @comb
    def wire():
        alu.a = a
        alu.b = b
        alu.op = 0
        result = alu.out


@module
def negedge_latch():
    clock = Input()
    d     = Input(8)
    q     = Output(8)

    @always(negedge(clock))
    def capture():
        q = d


@module
def wide_alu(width=8):
    a     = Input(width)
    b     = Input(width)
    carry = Output(width + 1)
    half  = Output(width - 1)

    @comb
    def compute():
        carry = a + b
        half = a


@module
def fixed_inverter():
    a   = Input(8)
    out = Output(8)

    @comb
    def invert():
        out = a ^ 255


@module
def multi_param(width=8, depth=4):
    addr = Input(depth)
    data = Input(width)
    out  = Output(width)

    @comb
    def passthrough():
        out = data


@module
def aug_counter(width=8):
    clock  = Input()
    enable = Input()
    count  = Output(width)
    cnt    = Register(width)

    @comb
    def drive():
        count = cnt

    @always(posedge(clock))
    def increment():
        if enable:
            cnt += 1


@module
def slice_writer():
    clock = Input()
    data  = Register(8)
    out   = Output(8)

    @comb
    def drive():
        out = data

    @always(posedge(clock))
    def write_nibble():
        data[3:0] = 0xA


@module
def mixed_locals(width=8):
    a   = Input(width)
    b   = Input(width)
    out = Output(width)

    @comb
    def compute():
        temp = int(a) + int(b)
        doubled = temp * 2
        out = doubled


# --- Tests ---

class TestModuleDecorator(TestBench):
    def create_module(self): return counter(width=4)

    def test_counter_simulation(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T
            self.assertEqual(self.get('count'), 0)
            dut.reset = 0
            for _ in range(5): yield T
            self.assertEqual(self.get('count'), 5)

    def test_counter_width(self):
        c = counter(width=4)
        sigs = c._signals()
        self.assertEqual(sigs['count'].width, 4)
        self.assertEqual(sigs['cnt'].width, 4)

    def test_counter_default_width(self):
        c = counter()
        self.assertEqual(c._signals()['count'].width, 8)

    def test_counter_params(self):
        c = counter(width=4)
        self.assertEqual(c._params, {'width': 4})

    def test_counter_type_name(self):
        c = counter(width=4)
        self.assertEqual(type(c).__name__, 'counter')


class TestMultipleInstances(TestBench):
    def create_module(self): return counter(width=4)

    def test_multiple_instances_independent(self):
        dut = self.dut; self.clock('clock', T)
        c2 = counter(width=4)
        @initial
        def _():
            dut.enable = 1; dut.reset = 0
            yield T; yield T
            self.assertEqual(self.get('count'), 2)
            self.assertEqual(int(c2.count), 0)


class TestSubModules(unittest.TestCase):
    def test_alu_simulation(self):
        a = simple_alu(width=8)
        a.a.set(10); a.b.set(3); a.op.set(0)
        a._settle_comb()
        self.assertEqual(int(a.out), 13)
        a.op.set(1)
        a._settle_comb()
        self.assertEqual(int(a.out), 7)

    def test_datapath_simulation(self):
        d = datapath(width=8)
        d.a.set(5); d.b.set(3)
        d._settle_comb()
        self.assertEqual(int(d.result), 8)

    def test_datapath_has_submodule(self):
        d = datapath(width=8)
        subs = d._submodules()
        self.assertIn('alu', subs)
        self.assertEqual(type(subs['alu']).__name__, 'simple_alu')


class TestVerilogEmission(unittest.TestCase):
    def test_counter_verilog(self):
        c = counter(width=4)
        v = c.to_verilog()
        self.assertIn('module counter', v)
        self.assertIn('parameter width = 4', v)
        self.assertIn('assign count = cnt', v)
        self.assertIn('always @(posedge clock)', v)

    def test_alu_verilog(self):
        a = simple_alu(width=16)
        v = a.to_verilog()
        self.assertIn('module simple_alu', v)
        self.assertIn('[width-1:0] a', v)

    def test_datapath_hierarchy(self):
        d = datapath(width=8)
        top = d.to_verilog()
        sub = d.alu.to_verilog()
        self.assertIn('module datapath', top)
        self.assertIn('simple_alu #(.width(width)) alu', top)
        self.assertIn('module simple_alu', sub)


class TestClassBasedUnchanged(TestBench):
    def create_module(self):
        from veripy import Module, Input, Output, Register

        class Counter(Module):
            def __init__(self, n=8):
                self.clock   = Input()
                self.reset   = Input()
                self.enable  = Input()
                self.count   = Output(n)
                self.counter = Register(n)
                super().__init__()

                @self.comb
                def drive():
                    self.count = self.counter

                @self.posedge(self.clock)
                def inc():
                    if self.reset:
                        self.counter = 0
                    elif self.enable:
                        self.counter = self.counter + 1

        return Counter(n=4)

    def test_class_counter(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1; dut.reset = 1; yield T; dut.reset = 0
            for _ in range(5): yield T
            self.assertEqual(self.get('count'), 5)

    def test_class_verilog(self):
        v = self.create_module().to_verilog()
        self.assertIn('module counter', v.lower())


class TestNegedge(unittest.TestCase):
    def test_negedge_verilog(self):
        m = negedge_latch()
        v = m.to_verilog()
        self.assertIn('always @(negedge clock)', v)
        self.assertIn('q <=', v)


class TestNegedgeSim(TestBench):
    def create_module(self): return negedge_latch()

    def test_negedge_captures_on_falling_edge(self):
        dut = self.dut
        @initial
        def _():
            dut.d = 42; dut.clock = 1; yield 1
            dut.clock = 0; yield 1
            self.assertEqual(self.get('q'), 42)


class TestParamExpr(unittest.TestCase):
    def test_carry_width(self):
        a = wide_alu(width=8)
        self.assertEqual(a._signals()['carry'].width, 9)

    def test_half_width(self):
        a = wide_alu(width=8)
        self.assertEqual(a._signals()['half'].width, 7)

    def test_param_expr_different_value(self):
        a = wide_alu(width=16)
        self.assertEqual(a._signals()['carry'].width, 17)
        self.assertEqual(a._signals()['half'].width, 15)

    def test_param_expr_verilog(self):
        a = wide_alu(width=8)
        v = a.to_verilog()
        self.assertIn('[width+1-1:0] carry', v)
        self.assertIn('[width-1-1:0] half', v)


class TestFixedWidth(unittest.TestCase):
    def test_no_params(self):
        m = fixed_inverter()
        self.assertEqual(m._params, {})

    def test_simulation(self):
        m = fixed_inverter()
        m.a.set(0x0F)
        m._settle_comb()
        self.assertEqual(int(m.out), 0xF0)

    def test_verilog_no_parameter_header(self):
        m = fixed_inverter()
        v = m.to_verilog()
        self.assertNotIn('parameter', v)
        self.assertIn('module fixed_inverter', v)


class TestMultipleParams(unittest.TestCase):
    def test_both_params_resolved(self):
        m = multi_param(width=16, depth=8)
        self.assertEqual(m._signals()['data'].width, 16)
        self.assertEqual(m._signals()['addr'].width, 8)

    def test_verilog_both_params(self):
        m = multi_param(width=16, depth=8)
        v = m.to_verilog()
        self.assertIn('parameter width = 16', v)
        self.assertIn('parameter depth = 8', v)


class TestAugmentedAssignment(TestBench):
    def create_module(self): return aug_counter(width=4)

    def test_aug_assign_simulation(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1
            for _ in range(3): yield T
            self.assertEqual(self.get('count'), 3)

    def test_aug_assign_wraps(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.enable = 1
            for _ in range(16): yield T
            self.assertEqual(self.get('count'), 0)


class TestSliceWrite(TestBench):
    def create_module(self): return slice_writer()

    def test_slice_write_simulation(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.data = 0x00; yield T
            self.assertEqual(self.get('out') & 0x0F, 0x0A)


class TestMixedLocals(unittest.TestCase):
    def test_local_not_rewritten(self):
        m = mixed_locals(width=8)
        m.a.set(3); m.b.set(4)
        m._settle_comb()
        self.assertEqual(int(m.out), 14)


class TestDeferredModule(unittest.TestCase):
    def test_deferred_from_parameter(self):
        from veripy.module import Module, DeferredModule

        class ALU(Module):
            def __init__(self, width=8):
                self.a = Input(width)
                self.out = Output(width)
                super().__init__()

        p = Parameter(default=8)
        p.name = 'width'
        d = ALU(p)
        self.assertIsInstance(d, DeferredModule)
        self.assertEqual(d.cls, ALU)

    def test_deferred_resolves(self):
        from veripy.module import Module, DeferredModule, _resolve_deferred

        class ALU(Module):
            def __init__(self, width=8):
                self.a = Input(width)
                self.out = Output(width)
                super().__init__()

        p = Parameter(default=8)
        p.name = 'width'
        d = ALU(p)
        resolved = _resolve_deferred(d, {'width': 16})
        self.assertIsInstance(resolved, ALU)
        self.assertEqual(resolved.a.width, 16)
        self.assertEqual(resolved.out.width, 16)


if __name__ == '__main__':
    unittest.main()
