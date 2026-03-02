"""Tests for enhanced pipeline: multi-value, stall, flush, valid, behavioral."""
import unittest
from veripy import Module, Input, Output, Register
from veripy.sim import SimEngine

T = 10


class TestMultiValuePipeline(unittest.TestCase):
    """Multi-value stages with tuple return and lambda unpack."""

    def test_two_values_through_pipeline(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: (int(self.a), int(self.b)))
                pipe.stage(lambda x, y: x + y)
                @self.comb
                def o():
                    self.out = pipe.result
        m = M()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.a.set(3); m.b.set(7)
            yield T   # stage 0 captures (3, 7)
            yield T   # stage 1 captures 3+7=10
            self.assertEqual(int(m.out), 10)
        sim.run()

    def test_three_values(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.x = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: (int(self.x), int(self.x) + 1, int(self.x) + 2))
                pipe.stage(lambda a, b, c: a + b + c)
                @self.comb
                def o():
                    self.out = pipe.result
        m = M()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.x.set(10)
            yield T; yield T
            self.assertEqual(int(m.out), 33)  # 10 + 11 + 12
        sim.run()

    def test_multi_value_registers_created(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: (int(self.a), int(self.a) * 2))
                pipe.stage(lambda x, y: x + y)
                @self.comb
                def o():
                    self.out = pipe.result
        m = M()
        v = m.to_verilog()
        # Stage 0 should have two registers
        self.assertIn('_pipe_stage0_0', v)
        self.assertIn('_pipe_stage0_1', v)
        # Stage 1 should have one register
        self.assertIn('_pipe_stage1', v)

    def test_chained_multi_value(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: (int(self.a), int(self.b))) \
                    .stage(lambda x, y: x + y)
                @self.comb
                def o():
                    self.out = pipe.result
        m = M()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.a.set(5); m.b.set(15)
            yield T; yield T
            self.assertEqual(int(m.out), 20)
        sim.run()


class TestPipelineStall(unittest.TestCase):
    def _make(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.stall = Input()
                self.d = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8,
                                     stall=self.stall)
                pipe.stage(lambda: int(self.d))
                pipe.stage(lambda prev: prev + 1)
                @self.comb
                def o():
                    self.out = pipe.result
        return M()

    def test_stall_freezes(self):
        m = self._make()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.d.set(10)
            yield T  # stage0=10, stage1=0+1=1
            yield T  # stage0=10, stage1=10+1=11
            val = int(m.out)
            self.assertEqual(val, 11)
            m.stall.set(1)
            yield T  # stalled — frozen
            self.assertEqual(int(m.out), val)
            yield T  # still frozen
            self.assertEqual(int(m.out), val)
            m.stall.set(0)
            yield T  # resumes
        sim.run()

    def test_stall_release(self):
        m = self._make()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.d.set(5)
            yield T; yield T
            val_before = int(m.out)  # 5+1=6
            m.stall.set(1)
            yield T; yield T
            self.assertEqual(int(m.out), val_before)  # still 6
            m.stall.set(0)
            yield T
            # After release, pipeline resumes
            self.assertNotEqual(int(m.out), 0)
        sim.run()


class TestPipelineFlush(unittest.TestCase):
    def _make(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.flush = Input()
                self.d = Input(8)
                self.out = Output(8)
                self.valid = Output()
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8,
                                     flush=self.flush)
                pipe.stage(lambda: int(self.d))
                pipe.stage(lambda prev: prev + 1)
                @self.comb
                def o():
                    self.out = pipe.result
                    self.valid = pipe.valid_out
        return M()

    def test_flush_clears_valid(self):
        m = self._make()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.d.set(10)
            yield T; yield T
            self.assertEqual(int(m.valid), 1)
            m.flush.set(1); yield T
            self.assertEqual(int(m.valid), 0)
        sim.run()


class TestPipelineValidPropagation(unittest.TestCase):
    def test_valid_propagates_with_data(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.start = Input()
                self.d = Input(8)
                self.out = Output(8)
                self.valid = Output()
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8,
                                     valid_in=self.start)
                pipe.stage(lambda: int(self.d))
                pipe.stage(lambda prev: prev)
                pipe.stage(lambda prev: prev)
                @self.comb
                def o():
                    self.out = pipe.result
                    self.valid = pipe.valid_out
        m = M()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.d.set(42)
            # No start yet — valid should not propagate
            yield T; yield T; yield T
            self.assertEqual(int(m.valid), 0)
            # Assert start for one cycle
            m.start.set(1); yield T; m.start.set(0)
            # Valid takes 2 more cycles to reach output (3 stages total)
            yield T
            self.assertEqual(int(m.valid), 0)
            yield T
            self.assertEqual(int(m.valid), 1)
        sim.run()


class TestPipelineBehavioral(unittest.TestCase):
    def test_single_value(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                super().__init__()
                self.pipe = self.pipeline(self.clock, self.reset, width=8)
                self.pipe.stage(lambda: int(self.a) + int(self.b))
                self.pipe.stage(lambda prev: prev * 2)
        m = M()
        m.a.set(3); m.b.set(4)
        result = m.pipe.behavioral()
        self.assertEqual(result, 14)  # (3+4)*2

    def test_multi_value(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                super().__init__()
                self.pipe = self.pipeline(self.clock, self.reset, width=8)
                self.pipe.stage(lambda: (int(self.a), int(self.b)))
                self.pipe.stage(lambda x, y: x * y)
        m = M()
        m.a.set(6); m.b.set(7)
        result = m.pipe.behavioral()
        self.assertEqual(result, 42)

    def test_behavioral_matches_sim(self):
        """Behavioral result should match what the pipeline produces after latency."""
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                self.out = Output(8)
                super().__init__()
                self.pipe = self.pipeline(self.clock, self.reset, width=8)
                self.pipe.stage(lambda: (int(self.a), int(self.b)))
                self.pipe.stage(lambda x, y: x + y)
                @self.comb
                def o():
                    self.out = self.pipe.result
        m = M()
        m.a.set(11); m.b.set(22)
        behav = m.pipe.behavioral()

        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.a.set(11); m.b.set(22)
            yield T; yield T
            self.assertEqual(int(m.out), behav)
        sim.run()


class TestBackwardCompat(unittest.TestCase):
    """Existing single-value pipeline API must still work."""

    def test_single_value_latency(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.a) + int(self.b))
                pipe.stage(lambda prev: int(prev) * 2)
                @self.comb
                def o():
                    self.out = pipe.result
        m = M()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.a.set(3); m.b.set(4)
            yield T; yield T
            self.assertEqual(int(m.out), 14)
        sim.run()

    def test_single_value_chaining(self):
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.x = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.x)).stage(lambda prev: int(prev) + 1)
                @self.comb
                def o():
                    self.out = pipe.result
        m = M()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.x.set(10)
            yield T; yield T
            self.assertEqual(int(m.out), 11)
        sim.run()

    def test_result_is_signal(self):
        """pipe.result should return a Signal for single-value pipelines."""
        from veripy.signal import Signal
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.d = Input(8)
                super().__init__()
                self.pipe = self.pipeline(self.clock, self.reset, width=8)
                self.pipe.stage(lambda: int(self.d))
        m = M()
        self.assertIsInstance(m.pipe.result, Signal)


if __name__ == '__main__':
    unittest.main()


class TestPipelineVerilogLowering(unittest.TestCase):
    """Verilog emission must lower stage functions, not just shift registers."""

    def test_stage_logic_in_verilog(self):
        """Generated Verilog should contain actual stage computation."""
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.a) + int(self.b))
                pipe.stage(lambda prev: int(prev) * 2)
                @self.comb
                def o():
                    self.out = pipe.result
        v = M().to_verilog()
        # Verilog should contain the add and multiply, not just a shift
        # Currently emits: _pipe_stage0 <= _pipe_stage0 (wrong)
        # Should emit something like: _pipe_stage0 <= a + b;
        #                             _pipe_stage1 <= _pipe_stage0 * 2;
        self.assertNotIn('_pipe_stage0 <= _pipe_stage0', v,
                         "Stage 0 should compute a+b, not self-assign")

    def test_dual_path_pipeline(self):
        """Python sim and Verilog should agree on pipeline output."""
        import subprocess, tempfile, os
        class M(Module):
            def __init__(self):
                self.clock = Input()
                self.reset = Input()
                self.a = Input(8)
                self.b = Input(8)
                self.out = Output(8)
                super().__init__()
                pipe = self.pipeline(self.clock, self.reset, width=8)
                pipe.stage(lambda: int(self.a) + int(self.b))
                pipe.stage(lambda prev: int(prev) * 2)
                @self.comb
                def o():
                    self.out = pipe.result
        m = M()

        # Python sim
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        py_result = []
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.a.set(3); m.b.set(4)
            yield T; yield T
            py_result.append(int(m.out))
        sim.run()
        self.assertEqual(py_result[0], 14)  # (3+4)*2

        # Verilog sim
        dut_v = m.to_verilog()
        tb_v = """`timescale 1ns/1ns
module tb;
  reg clock, reset;
  reg [7:0] a, b;
  wire [7:0] out;
  m dut(.clock(clock), .reset(reset), .a(a), .b(b), .out(out));
  initial begin
    clock = 0; reset = 1; a = 3; b = 4;
    #10 reset = 0;
    #10;  // stage 0 captures
    #10;  // stage 1 captures
    $display("out=%0d", out);
    $finish;
  end
  always #5 clock = ~clock;
endmodule
"""
        with tempfile.TemporaryDirectory() as d:
            with open(f'{d}/m.v', 'w') as f:
                f.write(dut_v)
            with open(f'{d}/tb.v', 'w') as f:
                f.write(tb_v)
            r = subprocess.run(['iverilog', '-o', f'{d}/sim', f'{d}/tb.v', f'{d}/m.v'],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"iverilog failed:\n{r.stderr}")
            r = subprocess.run(['vvp', f'{d}/sim'], capture_output=True, text=True)
            for line in r.stdout.split('\n'):
                if line.startswith('out='):
                    rtl_result = int(line.split('=')[1])
                    self.assertEqual(rtl_result, 14,
                                     f"Verilog produced {rtl_result}, expected 14")
                    return
            self.fail("No output from Verilog sim")
