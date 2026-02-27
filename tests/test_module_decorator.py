"""Tests for the @module decorator API."""
import unittest

from veripy.signal import Input, Output, Register, Mem
from veripy.context import comb, posedge, negedge
from veripy.decorator import module
from veripy.parameter import Parameter


# --- Module definitions (at module level so inspect.getsource works) ---

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

    @posedge(clock)
    def increment():
        if reset:
            cnt = 0
        elif enable:
            cnt = cnt + 1


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


# --- Additional modules for gap coverage ---

@module
def negedge_latch():
    clock = Input()
    d     = Input(8)
    q     = Output(8)

    @negedge(clock)
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

    @posedge(clock)
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

    @posedge(clock)
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


class TestModuleDecorator(unittest.TestCase):
    """Basic @module functionality."""

    def test_counter_simulation(self):
        c = counter(width=4)
        c.enable.set(1)
        c.reset.set(1)
        c.tick()
        self.assertEqual(int(c.count), 0)
        c.reset.set(0)
        for _ in range(5):
            c.tick()
        self.assertEqual(int(c.count), 5)

    def test_counter_width(self):
        c = counter(width=4)
        sigs = c._signals()
        self.assertEqual(sigs['count'].width, 4)
        self.assertEqual(sigs['cnt'].width, 4)
        self.assertEqual(sigs['clock'].width, 1)

    def test_counter_default_width(self):
        c = counter()
        self.assertEqual(c._signals()['count'].width, 8)

    def test_counter_params(self):
        c = counter(width=4)
        self.assertEqual(c._params, {'width': 4})

    def test_counter_type_name(self):
        c = counter(width=4)
        self.assertEqual(type(c).__name__, 'counter')

    def test_multiple_instances_independent(self):
        c1 = counter(width=4)
        c2 = counter(width=4)
        c1.enable.set(1)
        c1.reset.set(0)
        c1.tick()
        c1.tick()
        self.assertEqual(int(c1.count), 2)
        self.assertEqual(int(c2.count), 0)


class TestSubModules(unittest.TestCase):
    """Sub-module instantiation and wiring."""

    def test_alu_simulation(self):
        a = simple_alu(width=8)
        a.a.set(10)
        a.b.set(3)
        a.op.set(0)
        a.tick()
        self.assertEqual(int(a.out), 13)
        a.op.set(1)
        a.tick()
        self.assertEqual(int(a.out), 7)

    def test_datapath_simulation(self):
        d = datapath(width=8)
        d.a.set(5)
        d.b.set(3)
        d.tick()
        self.assertEqual(int(d.result), 8)

    def test_datapath_has_submodule(self):
        d = datapath(width=8)
        subs = d._submodules()
        self.assertIn('alu', subs)
        self.assertEqual(type(subs['alu']).__name__, 'simple_alu')


class TestVerilogEmission(unittest.TestCase):
    """Verilog output from @module-created modules."""

    def test_counter_verilog(self):
        c = counter(width=4)
        v = c.to_verilog()
        self.assertIn('module counter', v)
        self.assertIn('parameter width = 4', v)
        self.assertIn('assign count = cnt', v)
        self.assertIn('always @(posedge clock)', v)
        self.assertIn('cnt <= 0', v)
        self.assertIn('cnt <= (cnt + 1)', v)

    def test_alu_verilog(self):
        a = simple_alu(width=16)
        v = a.to_verilog()
        self.assertIn('module simple_alu', v)
        self.assertIn('[15:0] a', v)
        self.assertIn('out = (a + b)', v)
        self.assertIn('out = (a - b)', v)

    def test_datapath_emit_all(self):
        from veripy.emit_verilog import VerilogEmitter
        d = datapath(width=8)
        v = VerilogEmitter(d).emit_all()
        # Should contain both alu and datapath definitions
        self.assertIn('module simple_alu', v)
        self.assertIn('module datapath', v)
        self.assertIn('simple_alu #(.width(8)) alu', v)


class TestClassBasedUnchanged(unittest.TestCase):
    """Verify old class-based API still works."""

    def test_class_counter(self):
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

        c = Counter(n=4)
        c.enable.set(1)
        c.reset.set(1)
        c.tick()
        c.reset.set(0)
        for _ in range(5):
            c.tick()
        self.assertEqual(int(c.count), 5)
        v = c.to_verilog()
        self.assertIn('module counter', v.lower())


class TestNegedge(unittest.TestCase):
    """@negedge decorator."""

    def test_negedge_captures_on_falling_edge(self):
        m = negedge_latch()
        m.d.set(42)
        m.clock.set(1)
        m.tick()
        m.clock.set(0)
        m.tick()
        self.assertEqual(int(m.q), 42)

    def test_negedge_verilog(self):
        m = negedge_latch()
        v = m.to_verilog()
        self.assertIn('always @(negedge clock)', v)
        self.assertIn('q <=', v)


class TestParamExpr(unittest.TestCase):
    """Parameter arithmetic (width + 1, width - 1)."""

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
        self.assertIn('[8:0] carry', v)
        self.assertIn('[6:0] half', v)


class TestFixedWidth(unittest.TestCase):
    """Module with no parameters."""

    def test_no_params(self):
        m = fixed_inverter()
        self.assertEqual(m._params, {})

    def test_simulation(self):
        m = fixed_inverter()
        m.a.set(0x0F)
        m.tick()
        self.assertEqual(int(m.out), 0xF0)

    def test_verilog_no_parameter_header(self):
        m = fixed_inverter()
        v = m.to_verilog()
        self.assertNotIn('parameter', v)
        self.assertIn('module fixed_inverter', v)


class TestMultipleParams(unittest.TestCase):
    """Module with multiple parameters."""

    def test_both_params_resolved(self):
        m = multi_param(width=16, depth=8)
        self.assertEqual(m._signals()['data'].width, 16)
        self.assertEqual(m._signals()['addr'].width, 8)
        self.assertEqual(m._params, {'width': 16, 'depth': 8})

    def test_verilog_both_params(self):
        m = multi_param(width=16, depth=8)
        v = m.to_verilog()
        self.assertIn('parameter width = 16', v)
        self.assertIn('parameter depth = 8', v)


class TestAugmentedAssignment(unittest.TestCase):
    """Augmented assignment (cnt += 1) in logic blocks."""

    def test_aug_assign_simulation(self):
        c = aug_counter(width=4)
        c.enable.set(1)
        for _ in range(3):
            c.tick()
        self.assertEqual(int(c.count), 3)

    def test_aug_assign_wraps(self):
        c = aug_counter(width=4)
        c.enable.set(1)
        for _ in range(16):
            c.tick()
        self.assertEqual(int(c.count), 0)


class TestSliceWrite(unittest.TestCase):
    """Subscript assignment (data[3:0] = val)."""

    def test_slice_write_simulation(self):
        m = slice_writer()
        m.data.set(0x00)
        m.tick()
        self.assertEqual(int(m.out) & 0x0F, 0x0A)


class TestMixedLocals(unittest.TestCase):
    """Normal Python locals coexisting with signals."""

    def test_local_not_rewritten(self):
        m = mixed_locals(width=8)
        m.a.set(3)
        m.b.set(4)
        m.tick()
        self.assertEqual(int(m.out), 14)  # (3+4)*2


class TestDeferredModule(unittest.TestCase):
    """DeferredModule via Module.__new__ with Parameter args."""

    def test_deferred_created(self):
        from veripy.module import DeferredModule
        p = Parameter(default=8)
        p.name = 'width'
        d = simple_alu(width=8)  # concrete — normal Module
        self.assertNotIsInstance(d, DeferredModule)

    def test_deferred_from_parameter(self):
        from veripy.module import Module, DeferredModule, _resolve_deferred

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
