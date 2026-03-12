"""Tests for pipeline transforms."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.verify import TestBench, initial

T = 10


def _make_pipe(width=8):
    class Pipe(Module):
        def __init__(self):
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


class TestPipelineVerilog(unittest.TestCase):
    def setUp(self):
        self.v = _make_pipe().to_verilog()

    def test_reg_declarations(self):
        self.assertIn('reg [7:0] _pipe_stage0', self.v)
        self.assertIn('reg [7:0] _pipe_stage1', self.v)

    def test_posedge_block(self):
        self.assertIn('always @(posedge clock)', self.v)

    def test_reset_logic(self):
        self.assertIn('_pipe_stage0 <= 0', self.v)
        self.assertIn('_pipe_stage1 <= 0', self.v)

    def test_stage_chain(self):
        self.assertIn('_pipe_stage0 <= (a + b)', self.v)
        self.assertIn('_pipe_stage1 <= (_pipe_stage0 * 2)', self.v)

    def test_output_wired(self):
        self.assertIn('assign out = _pipe_stage1', self.v)


class TestPipelineLatency(TestBench):
    def create_module(self): return _make_pipe()

    def test_pipeline_latency(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 3; dut.b = 4
            yield T
            self.assertEqual(self.get('out'), 0)
            yield T
            self.assertEqual(self.get('out'), 14)

    def test_pipeline_reset(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.a = 5; dut.b = 5
            yield T; yield T
            self.assertEqual(self.get('out'), 20)
            dut.reset = 1; yield T
            self.assertEqual(self.get('out'), 0)


class TestPipelineChaining(TestBench):
    def create_module(self):
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
        return P()

    def test_pipeline_chaining(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.x = 10
            yield T; yield T
            self.assertEqual(self.get('out'), 11)


class TestThreeStages(TestBench):
    def create_module(self):
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
        return P()

    def test_three_stages(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.d = 10
            yield T; yield T; yield T
            self.assertEqual(self.get('out'), 12)


if __name__ == '__main__':
    unittest.main()
