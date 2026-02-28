"""Tests for pipeline transforms."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.sim import SimEngine

T = 10


class TestPipelineVerilog(unittest.TestCase):
    """Pipeline Verilog emission."""

    def _make_pipe(self):
        class Pipe(Module):
            def __init__(self, width=8):
                self.clock = Input()
                self.reset = Input()
                self.a     = Input(width)
                self.b     = Input(width)
                self.out   = Output(width)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=width)
                pipe.stage(lambda: int(self.a) + int(self.b))
                pipe.stage(lambda prev: int(prev) * 2)
                @self.comb
                def output():
                    self.out = pipe.result
        return Pipe()

    def test_reg_declarations(self):
        v = self._make_pipe().to_verilog()
        self.assertIn('reg [7:0] _pipe_stage0', v)
        self.assertIn('reg [7:0] _pipe_stage1', v)

    def test_posedge_block(self):
        v = self._make_pipe().to_verilog()
        self.assertIn('always @(posedge clock)', v)

    def test_reset_logic(self):
        v = self._make_pipe().to_verilog()
        self.assertIn('_pipe_stage0 <= 0', v)
        self.assertIn('_pipe_stage1 <= 0', v)

    def test_stage_chain(self):
        v = self._make_pipe().to_verilog()
        self.assertIn('_pipe_stage1 <= _pipe_stage0', v)

    def test_output_wired(self):
        v = self._make_pipe().to_verilog()
        self.assertIn('assign out = _pipe_stage1', v)


class TestPipeline(unittest.TestCase):
    def _make_pipe(self):
        class Pipe(Module):
            def __init__(self, width=8):
                self.clock  = Input()
                self.reset  = Input()
                self.a      = Input(width)
                self.b      = Input(width)
                self.out    = Output(width)
                super().__init__()

                pipe = self.pipeline(self.clock, self.reset, width=width)
                pipe.stage(lambda: int(self.a) + int(self.b))
                pipe.stage(lambda prev: int(prev) * 2)

                @self.comb
                def output():
                    self.out = pipe.result

        return Pipe()

    def test_pipeline_latency(self):
        m = self._make_pipe()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.a.set(3); m.b.set(4)
            yield T  # stage 0 captures a+b=7
            self.assertEqual(int(m.out), 0)  # stage 1 still has reset value
            yield T  # stage 1 captures 7*2=14
            self.assertEqual(int(m.out), 14)
        sim.run()

    def test_pipeline_reset(self):
        m = self._make_pipe()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.a.set(5); m.b.set(5)
            yield T; yield T
            self.assertEqual(int(m.out), 20)
            m.reset.set(1); yield T
            self.assertEqual(int(m.out), 0)
        sim.run()

    def test_pipeline_chaining(self):
        class P(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.x     = Input(8)
                self.out   = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.x)).stage(lambda prev: int(prev) + 1)
                @self.comb
                def o():
                    self.out = pipe.result
        m = P()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.x.set(10)
            yield T; yield T
            self.assertEqual(int(m.out), 11)
        sim.run()

    def test_three_stages(self):
        class P(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.d     = Input(8)
                self.out   = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.d))
                pipe.stage(lambda prev: int(prev) + 1)
                pipe.stage(lambda prev: int(prev) + 1)
                @self.comb
                def o():
                    self.out = pipe.result
        m = P()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.d.set(10)
            yield T  # stage0=10, stage1=0+1=1, stage2=0+1=1
            yield T  # stage0=10, stage1=11, stage2=1+1=2
            yield T  # stage0=10, stage1=11, stage2=11+1=12
            self.assertEqual(int(m.out), 12)  # 10 + 1 + 1
        sim.run()


if __name__ == '__main__':
    unittest.main()
