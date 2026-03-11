"""Tests for enhanced pipeline: multi-value, stall, flush, valid, behavioral."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.verify import TestBench, initial

T = 10


class _TwoValModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.a = Input(8); self.b = Input(8); self.out = Output(8)
        super().__init__()
        pipe = self.pipeline(self.clock, self.reset, width=8)
        pipe.stage(lambda: (int(self.a), int(self.b)))
        pipe.stage(lambda x, y: x + y)
        @self.comb
        def o(): self.out = pipe.result


class TestMultiValuePipeline(TestBench):
    def create_module(self): return _TwoValModule()

    def test_two_values_through_pipeline(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 3; dut.b = 7
            yield T; yield T
            self.assertEqual(self.get('out'), 10)

    def test_chained_multi_value(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 5; dut.b = 15
            yield T; yield T
            self.assertEqual(self.get('out'), 20)


class _ThreeValModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.x = Input(8); self.out = Output(8)
        super().__init__()
        pipe = self.pipeline(self.clock, self.reset, width=8)
        pipe.stage(lambda: (int(self.x), int(self.x) + 1, int(self.x) + 2))
        pipe.stage(lambda a, b, c: a + b + c)
        @self.comb
        def o(): self.out = pipe.result


class TestThreeValues(TestBench):
    def create_module(self): return _ThreeValModule()

    def test_three_values(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.x = 10; yield T; yield T
            self.assertEqual(self.get('out'), 33)


class TestMultiValueRegisters(unittest.TestCase):
    def test_multi_value_registers_created(self):
        class M(Module):
            def __init__(self):
                self.clock = Input(); self.reset = Input()
                self.a = Input(8); self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: (int(self.a), int(self.a) * 2))
                pipe.stage(lambda x, y: x + y)
                @self.comb
                def o(): self.out = pipe.result
        v = M().to_verilog()
        self.assertIn('_pipe_stage0_0', v)
        self.assertIn('_pipe_stage0_1', v)
        self.assertIn('_pipe_stage1', v)


class _StallModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.stall = Input(); self.d = Input(8); self.out = Output(8)
        super().__init__()
        pipe = self.pipeline(self.clock, self.reset, width=8, stall=self.stall)
        pipe.stage(lambda: int(self.d))
        pipe.stage(lambda prev: prev + 1)
        @self.comb
        def o(): self.out = pipe.result


class TestPipelineStall(TestBench):
    def create_module(self): return _StallModule()

    def test_stall_freezes(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.d = 10; yield T; yield T
            val = self.get('out')
            self.assertEqual(val, 11)
            dut.stall = 1; yield T
            self.assertEqual(self.get('out'), val)
            yield T
            self.assertEqual(self.get('out'), val)
            dut.stall = 0; yield T

    def test_stall_release(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.d = 5; yield T; yield T
            val_before = self.get('out')
            dut.stall = 1; yield T; yield T
            self.assertEqual(self.get('out'), val_before)
            dut.stall = 0; yield T
            self.assertNotEqual(self.get('out'), 0)


class _FlushModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.flush = Input(); self.d = Input(8)
        self.out = Output(8); self.valid = Output()
        super().__init__()
        pipe = self.pipeline(self.clock, self.reset, width=8, flush=self.flush)
        pipe.stage(lambda: int(self.d))
        pipe.stage(lambda prev: prev + 1)
        @self.comb
        def o():
            self.out = pipe.result
            self.valid = pipe.valid_out


class TestPipelineFlush(TestBench):
    def create_module(self): return _FlushModule()

    def test_flush_clears_valid(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.d = 10; yield T; yield T
            self.assertEqual(self.get('valid'), 1)
            dut.flush = 1; yield T
            self.assertEqual(self.get('valid'), 0)


class _ValidPropModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.start = Input(); self.d = Input(8)
        self.out = Output(8); self.valid = Output()
        super().__init__()
        pipe = self.pipeline(self.clock, self.reset, width=8, valid_in=self.start)
        pipe.stage(lambda: int(self.d))
        pipe.stage(lambda prev: prev)
        pipe.stage(lambda prev: prev)
        @self.comb
        def o():
            self.out = pipe.result
            self.valid = pipe.valid_out


class TestPipelineValidPropagation(TestBench):
    def create_module(self): return _ValidPropModule()

    def test_valid_propagates_with_data(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.d = 42
            yield T; yield T; yield T
            self.assertEqual(self.get('valid'), 0)
            dut.start = 1; yield T; dut.start = 0
            yield T
            self.assertEqual(self.get('valid'), 0)
            yield T
            self.assertEqual(self.get('valid'), 1)


class TestPipelineBehavioral(unittest.TestCase):
    def test_single_value(self):
        class M(Module):
            def __init__(self):
                self.clock = Input(); self.reset = Input()
                self.a = Input(8); self.b = Input(8)
                super().__init__()
                self.pipe = self.pipeline(self.clock, self.reset, width=8)
                self.pipe.stage(lambda: int(self.a) + int(self.b))
                self.pipe.stage(lambda prev: prev * 2)
        m = M()
        m.a.set(3); m.b.set(4)
        self.assertEqual(m.pipe.behavioral(), 14)

    def test_multi_value(self):
        class M(Module):
            def __init__(self):
                self.clock = Input(); self.reset = Input()
                self.a = Input(8); self.b = Input(8)
                super().__init__()
                self.pipe = self.pipeline(self.clock, self.reset, width=8)
                self.pipe.stage(lambda: (int(self.a), int(self.b)))
                self.pipe.stage(lambda x, y: x * y)
        m = M()
        m.a.set(6); m.b.set(7)
        self.assertEqual(m.pipe.behavioral(), 42)


class _BehavMatchModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.a = Input(8); self.b = Input(8); self.out = Output(8)
        super().__init__()
        self.pipe = self.pipeline(self.clock, self.reset, width=8)
        self.pipe.stage(lambda: (int(self.a), int(self.b)))
        self.pipe.stage(lambda x, y: x + y)
        @self.comb
        def o(): self.out = self.pipe.result


class TestBehavioralMatchesSim(TestBench):
    def create_module(self): return _BehavMatchModule()

    def test_behavioral_matches_sim(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 11; dut.b = 22
            yield T; yield T
            self.assertEqual(self.get('out'), self._mod.pipe.behavioral())


class TestBackwardCompat(unittest.TestCase):
    def test_result_is_signal(self):
        from veripy.signal import Signal
        class M(Module):
            def __init__(self):
                self.clock = Input(); self.reset = Input(); self.d = Input(8)
                super().__init__()
                self.pipe = self.pipeline(self.clock, self.reset, width=8)
                self.pipe.stage(lambda: int(self.d))
        self.assertIsInstance(M().pipe.result, Signal)


class _SingleValModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.a = Input(8); self.b = Input(8); self.out = Output(8)
        super().__init__()
        pipe = self.pipeline(self.clock, self.reset, width=8)
        pipe.stage(lambda: int(self.a) + int(self.b))
        pipe.stage(lambda prev: int(prev) * 2)
        @self.comb
        def o(): self.out = pipe.result


class TestSingleValueLatency(TestBench):
    def create_module(self): return _SingleValModule()

    def test_single_value_latency(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 3; dut.b = 4; yield T; yield T
            self.assertEqual(self.get('out'), 14)


class _ChainModule(Module):
    def __init__(self):
        self.clock = Input(); self.reset = Input()
        self.x = Input(8); self.out = Output(8)
        super().__init__()
        pipe = self.pipeline(self.clock, self.reset, width=8)
        pipe.stage(lambda: int(self.x)).stage(lambda prev: int(prev) + 1)
        @self.comb
        def o(): self.out = pipe.result


class TestSingleValueChaining(TestBench):
    def create_module(self): return _ChainModule()

    def test_single_value_chaining(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.x = 10; yield T; yield T
            self.assertEqual(self.get('out'), 11)


class TestPipelineVerilogLowering(unittest.TestCase):
    def test_stage_logic_in_verilog(self):
        class M(Module):
            def __init__(self):
                self.clock = Input(); self.reset = Input()
                self.a = Input(8); self.b = Input(8); self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.a) + int(self.b))
                pipe.stage(lambda prev: int(prev) * 2)
                @self.comb
                def o(): self.out = pipe.result
        v = M().to_verilog()
        self.assertNotIn('_pipe_stage0 <= _pipe_stage0', v,
                         "Stage 0 should compute a+b, not self-assign")

    def test_dual_path_pipeline(self):
        import subprocess, tempfile, os
        class M(Module):
            def __init__(self):
                self.clock = Input(); self.reset = Input()
                self.a = Input(8); self.b = Input(8); self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.a) + int(self.b))
                pipe.stage(lambda prev: int(prev) * 2)
                @self.comb
                def o(): self.out = pipe.result
        dut_v = M().to_verilog()
        tb_v = """`timescale 1ns/1ns
module tb;
  reg clock, reset;
  reg [7:0] a, b;
  wire [7:0] out;
  m dut(.clock(clock), .reset(reset), .a(a), .b(b), .out(out));
  initial begin
    clock = 0; reset = 1; a = 3; b = 4;
    #10 reset = 0;
    #10; #10;
    $display("out=%0d", out);
    $finish;
  end
  always #5 clock = ~clock;
endmodule
"""
        with tempfile.TemporaryDirectory() as d:
            with open(f'{d}/m.v', 'w') as f: f.write(dut_v)
            with open(f'{d}/tb.v', 'w') as f: f.write(tb_v)
            r = subprocess.run(['iverilog', '-o', f'{d}/sim', f'{d}/tb.v', f'{d}/m.v'],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"iverilog failed:\n{r.stderr}")
            r = subprocess.run(['vvp', f'{d}/sim'], capture_output=True, text=True)
            for line in r.stdout.split('\n'):
                if line.startswith('out='):
                    self.assertEqual(int(line.split('=')[1]), 14)
                    return
            self.fail("No output from Verilog sim")


if __name__ == '__main__':
    unittest.main()
