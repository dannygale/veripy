"""Tests for named-stage pipeline API: pipe.stage('name', field=source, ...)."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.sim import SimEngine

PERIOD = 10  # clock period for sim.clock()


def _run(module, stim_fn):
    """Helper: clock + run stimulus."""
    sim = SimEngine(module)
    sim.clock(module.clk, PERIOD)
    sim.initial(stim_fn)
    sim.run()


# ── basic register creation and latching ─────────────────────────


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

    def test_latch(self):
        m = self._make()
        def stim():
            m.rst.set(1); m.a.set(10); m.b.set(20)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 10)
            self.assertEqual(int(m.s0_b), 20)
        _run(m, stim)

    def test_reset_zeros(self):
        m = self._make()
        def stim():
            m.rst.set(0); m.a.set(42); m.b.set(99)
            yield PERIOD  # latch values
            self.assertEqual(int(m.s0_a), 42)
            m.rst.set(1)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 0)
            self.assertEqual(int(m.s0_b), 0)
        _run(m, stim)

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


# ── stall ────────────────────────────────────────────────────────


class TestNamedStageStall(unittest.TestCase):

    def _make(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.a = Input(8)
                self.stall = Input()
                super().__init__()
                pipe = self.pipeline(self.clk, self.rst)
                self.s0 = pipe.stage('s0', self.stall, None, a=self.a)
        return M()

    def test_stall_holds(self):
        m = self._make()
        def stim():
            m.rst.set(1); m.stall.set(0); m.a.set(5)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 5)
            m.a.set(99); m.stall.set(1)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 5)  # held
        _run(m, stim)

    def test_stall_release(self):
        m = self._make()
        def stim():
            m.rst.set(1); m.stall.set(0); m.a.set(5)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD
            m.a.set(99); m.stall.set(1)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 5)
            m.stall.set(0)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 99)
        _run(m, stim)


# ── flush ────────────────────────────────────────────────────────


class TestNamedStageFlush(unittest.TestCase):

    def test_flush_zeros(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.a = Input(8)
                self.flush = Input()
                super().__init__()
                pipe = self.pipeline(self.clk, self.rst)
                self.s0 = pipe.stage('s0', None, self.flush, a=self.a)
        m = M()
        def stim():
            m.rst.set(1); m.flush.set(0); m.a.set(42)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 42)
            m.flush.set(1)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 0)
        _run(m, stim)


# ── multi-stage with cross-stage references ──────────────────────


class TestNamedStageMultiStage(unittest.TestCase):

    def _make(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.a = Input(8)
                self.stall = Input()
                super().__init__()
                pipe = self.pipeline(self.clk, self.rst)
                self.s0 = pipe.stage('s0', a=self.a)
                self.s1 = pipe.stage('s1', self.stall, None,
                                     a=self.s0.a)
        return M()

    def test_two_stage_latency(self):
        """Data takes two cycles to propagate through two stages."""
        m = self._make()
        def stim():
            m.rst.set(1); m.stall.set(0); m.a.set(7)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD  # posedge: s0 latches 7, s1 latches old s0 (0)
            self.assertEqual(int(m.s0_a), 7)
            self.assertEqual(int(m.s1_a), 0)
            yield PERIOD  # posedge: s1 latches 7
            self.assertEqual(int(m.s1_a), 7)
        _run(m, stim)

    def test_per_stage_stall(self):
        """Stalling s1 doesn't affect s0."""
        m = self._make()
        def stim():
            m.rst.set(1); m.stall.set(0); m.a.set(7)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD * 2  # both stages have 7
            self.assertEqual(int(m.s1_a), 7)
            m.a.set(50); m.stall.set(1)
            yield PERIOD
            self.assertEqual(int(m.s0_a), 50)  # s0 advanced
            self.assertEqual(int(m.s1_a), 7)   # s1 held
        _run(m, stim)


# ── valid propagation ────────────────────────────────────────────


class TestNamedStageValid(unittest.TestCase):

    def test_valid_propagates(self):
        class M(Module):
            def __init__(self):
                self.clk = Input()
                self.rst = Input()
                self.a = Input(8)
                self.flush = Input()
                super().__init__()
                self.vin = Register(1)
                pipe = self.pipeline(self.clk, self.rst)
                self.s0 = pipe.stage('s0', None, self.flush,
                                     a=self.a, valid=self.vin)
                self.s1 = pipe.stage('s1', None, self.flush,
                                     a=self.s0.a, valid=self.s0.valid)
                @self.comb
                def _():
                    self.vin = 1
        m = M()
        def stim():
            m.rst.set(1); m.flush.set(0); m.a.set(1)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD  # s0 valid=1, s1 valid=0
            self.assertEqual(int(m.s0_valid), 1)
            self.assertEqual(int(m.s1_valid), 0)
            yield PERIOD  # s1 valid=1
            self.assertEqual(int(m.s1_valid), 1)
            m.flush.set(1)
            yield PERIOD
            self.assertEqual(int(m.s0_valid), 0)
            self.assertEqual(int(m.s1_valid), 0)
        _run(m, stim)


# ── Verilog emission ─────────────────────────────────────────────


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
                s0 = pipe.stage('s0', self.stall, self.flush,
                                a=self.a)
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


# ── coexistence with lambda-chain API ────────────────────────────


class TestNamedStageCoexistence(unittest.TestCase):

    def test_lambda_chain_still_works(self):
        """Lambda-chain API is unaffected by named-stage additions."""
        class M(Module):
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
        m = M()
        sim = SimEngine(m)
        sim.clock(m.clk, PERIOD)
        def stim():
            m.rst.set(1); m.inp.set(10)
            yield PERIOD
            m.rst.set(0)
            yield PERIOD * 2
            self.assertEqual(int(m.out), 11)
        sim.initial(stim)
        sim.run()


if __name__ == '__main__':
    unittest.main()
