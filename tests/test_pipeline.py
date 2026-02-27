"""Tests for pipeline transforms."""
import unittest
from veripy import Module, Input, Output, Register


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
        """Result appears after N stages of clock cycles."""
        m = self._make_pipe()
        m.reset.set(1); m.tick(); m.reset.set(0)
        m.a.set(3); m.b.set(4)
        m.tick()  # stage 0 captures a+b=7
        self.assertEqual(int(m.out), 0)  # stage 1 still has reset value
        m.tick()  # stage 1 captures 7*2=14
        self.assertEqual(int(m.out), 14)

    def test_pipeline_reset(self):
        """Reset clears all stages."""
        m = self._make_pipe()
        m.a.set(5); m.b.set(5)
        m.tick(); m.tick()
        self.assertEqual(int(m.out), 20)
        m.reset.set(1); m.tick()
        self.assertEqual(int(m.out), 0)

    def test_pipeline_chaining(self):
        """stage() returns self for chaining."""
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
        m.reset.set(1); m.tick(); m.reset.set(0)
        m.x.set(10)
        m.tick(); m.tick()
        self.assertEqual(int(m.out), 11)

    def test_three_stages(self):
        """Three-stage pipeline has 3-cycle latency for input data."""
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
        m.reset.set(1); m.tick(); m.reset.set(0)
        m.d.set(10)
        m.tick()  # stage0=10, stage1=0+1=1, stage2=0+1=1
        m.tick()  # stage0=10, stage1=11, stage2=1+1=2
        m.tick()  # stage0=10, stage1=11, stage2=11+1=12
        self.assertEqual(int(m.out), 12)  # 10 + 1 + 1


if __name__ == '__main__':
    unittest.main()
