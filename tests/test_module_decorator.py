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


if __name__ == '__main__':
    unittest.main()
