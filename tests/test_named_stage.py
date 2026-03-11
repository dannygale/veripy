"""Tests for named-stage pipeline API: pipe.stage('name', field=source, ...)."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.verify import TestBench, initial

PERIOD = 10


class TestNamedStageBasic(unittest.TestCase):

    def _make(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.a = Input(8)
                self.b = Input(8)
                super().__init__()
                pipe = self.pipeline(self.clk, self.rst)
                self.s0 = pipe.stage('s0', a=self.a, b=self.b)
        return M()

    def test_registers_created(self):
        m = self._make()
        self.assertTrue(hasattr(m, 's0_a'))
        self.assertTrue(hasattr(m, 's0_b'))
        self.assertEqual(m.s0_a.width, 8)
        self.assertEqual(m.s0_b.width, 8)

    def test_field_access(self):
        m = self._make()
        self.assertIs(m.s0.a, m.s0_a)
        self.assertIs(m.s0.b, m.s0_b)

    def test_bad_field_raises(self):
        m = self._make()
        with self.assertRaises(AttributeError):
            _ = m.s0.nonexistent

    def test_width_inferred_from_source(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.wide = Input(32)
                self.narrow = Input(1)
                super().__init__()
                pipe = self.pipeline(self.clk, self.rst)
                self.s = pipe.stage('s', w=self.wide, n=self.narrow)
        m = M()
        self.assertEqual(m.s_w.width, 32)
        self.assertEqual(m.s_n.width, 1)


class _NamedStageModule(Module):
    def __init__(self):
        self.clk = Input()
        self.rst = Input()
        self.a = Input(8)
        self.b = Input(8)
        super().__init__()
        pipe = self.pipeline(self.clk, self.rst)
        self.s0 = pipe.stage('s0', a=self.a, b=self.b)


class TestNamedStageLatch(TestBench):
    def create_module(self): return _NamedStageModule()

    def test_latch(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.a = 10; dut.b = 20; yield PERIOD
            dut.rst = 0; yield PERIOD
            self.assertEqual(self.get('s0_a'), 10)
            self.assertEqual(self.get('s0_b'), 20)

    def test_reset_zeros(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 0; dut.a = 42; dut.b = 99; yield PERIOD
            self.assertEqual(self.get('s0_a'), 42)
            dut.rst = 1; yield PERIOD
            self.assertEqual(self.get('s0_a'), 0)
            self.assertEqual(self.get('s0_b'), 0)


class _StallModule(Module):
    def __init__(self):
        self.clk = Input()
        self.rst = Input()
        self.a = Input(8)
        self.stall = Input()
        super().__init__()
        pipe = self.pipeline(self.clk, self.rst)
        self.s0 = pipe.stage('s0', self.stall, None, a=self.a)


class TestNamedStageStall(TestBench):
    def create_module(self): return _StallModule()

    def test_stall_holds(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.stall = 0; dut.a = 5; yield PERIOD
            dut.rst = 0; yield PERIOD
            self.assertEqual(self.get('s0_a'), 5)
            dut.a = 99; dut.stall = 1; yield PERIOD
            self.assertEqual(self.get('s0_a'), 5)

    def test_stall_release(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.stall = 0; dut.a = 5; yield PERIOD
            dut.rst = 0; yield PERIOD
            dut.a = 99; dut.stall = 1; yield PERIOD
            self.assertEqual(self.get('s0_a'), 5)
            dut.stall = 0; yield PERIOD
            self.assertEqual(self.get('s0_a'), 99)


class _FlushModule(Module):
    def __init__(self):
        self.clk = Input()
        self.rst = Input()
        self.a = Input(8)
        self.flush = Input()
        super().__init__()
        pipe = self.pipeline(self.clk, self.rst)
        self.s0 = pipe.stage('s0', None, self.flush, a=self.a)


class TestNamedStageFlush(TestBench):
    def create_module(self): return _FlushModule()

    def test_flush_zeros(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.flush = 0; dut.a = 42; yield PERIOD
            dut.rst = 0; yield PERIOD
            self.assertEqual(self.get('s0_a'), 42)
            dut.flush = 1; yield PERIOD
            self.assertEqual(self.get('s0_a'), 0)


class _TwoStageModule(Module):
    def __init__(self):
        self.clk = Input()
        self.rst = Input()
        self.a = Input(8)
        self.stall = Input()
        super().__init__()
        pipe = self.pipeline(self.clk, self.rst)
        self.s0 = pipe.stage('s0', a=self.a)
        self.s1 = pipe.stage('s1', self.stall, None, a=self.s0.a)


class TestNamedStageMultiStage(TestBench):
    def create_module(self): return _TwoStageModule()

    def test_two_stage_latency(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.stall = 0; dut.a = 7; yield PERIOD
            dut.rst = 0; yield PERIOD
            self.assertEqual(self.get('s0_a'), 7)
            self.assertEqual(self.get('s1_a'), 0)
            yield PERIOD
            self.assertEqual(self.get('s1_a'), 7)

    def test_per_stage_stall(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.stall = 0; dut.a = 7; yield PERIOD
            dut.rst = 0; yield PERIOD * 2
            self.assertEqual(self.get('s1_a'), 7)
            dut.a = 50; dut.stall = 1; yield PERIOD
            self.assertEqual(self.get('s0_a'), 50)
            self.assertEqual(self.get('s1_a'), 7)


class _ValidModule(Module):
    def __init__(self):
        self.clk = Input()
        self.rst = Input()
        self.a = Input(8)
        self.flush = Input()
        self.vin = Register(1)
        super().__init__()
        pipe = self.pipeline(self.clk, self.rst)
        self.s0 = pipe.stage('s0', None, self.flush, a=self.a, valid=self.vin)
        self.s1 = pipe.stage('s1', None, self.flush, a=self.s0.a, valid=self.s0.valid)

        @self.comb
        def _():
            self.vin = 1


class TestNamedStageValid(TestBench):
    def create_module(self): return _ValidModule()

    def test_valid_propagates(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.flush = 0; dut.a = 1; yield PERIOD
            dut.rst = 0; yield PERIOD
            self.assertEqual(self.get('s0_valid'), 1)
            self.assertEqual(self.get('s1_valid'), 0)
            yield PERIOD
            self.assertEqual(self.get('s1_valid'), 1)
            dut.flush = 1; yield PERIOD
            self.assertEqual(self.get('s0_valid'), 0)
            self.assertEqual(self.get('s1_valid'), 0)


class TestNamedStageVerilog(unittest.TestCase):

    def _make(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.a = Input(8)
                self.stall = Input()
                self.flush = Input()
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clk, self.rst)
                s0 = pipe.stage('s0', self.stall, self.flush, a=self.a)
                @self.comb
                def _():
                    self.out = s0.a
        return M()

    def test_reg_declared(self):
        v = self._make().to_verilog()
        self.assertIn('reg [7:0] s0_a', v)

    def test_reset_block(self):
        v = self._make().to_verilog()
        self.assertIn('s0_a <= 0', v)

    def test_stall_block(self):
        v = self._make().to_verilog()
        self.assertIn('if (stall)', v)

    def test_flush_block(self):
        v = self._make().to_verilog()
        self.assertIn('if (flush)', v)

    def test_advance_assignment(self):
        v = self._make().to_verilog()
        self.assertIn('s0_a <= a', v)

    def test_multi_stage_verilog(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.a = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clk, self.rst)
                s0 = pipe.stage('s0', a=self.a)
                s1 = pipe.stage('s1', a=s0.a)
                @self.comb
                def _():
                    self.out = s1.a
        v = M().to_verilog()
        self.assertIn('s0_a <= a', v)
        self.assertIn('s1_a <= s0_a', v)


class _LambdaModule(Module):
    def __init__(self):
        self.clk = Input()
        self.rst = Input()
        self.inp = Input(8)
        self.out = Output(8)
        super().__init__()
        pipe = self.pipeline(self.clk, self.rst, width=8)
        pipe.stage(lambda: int(self.inp))
        pipe.stage(lambda x: x + 1)
        @self.comb
        def _():
            self.out = pipe.result


class TestNamedStageCoexistence(TestBench):
    def create_module(self): return _LambdaModule()

    def test_lambda_chain_still_works(self):
        dut = self.dut; self.clock('clk', PERIOD)
        @initial
        def _():
            dut.rst = 1; dut.inp = 10; yield PERIOD
            dut.rst = 0; yield PERIOD * 2
            self.assertEqual(self.get('out'), 11)


if __name__ == '__main__':
    unittest.main()
