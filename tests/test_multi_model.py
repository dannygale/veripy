"""Tests for multi-model dispatch — removed APIs replaced with stubs."""
import os
import unittest

from veripy import Module, Input, Output, Register, TestBench


# These APIs were removed when SimEngine/behavioral backend was removed.
# The tests below verify the current TestBench behavior.

class TestTestBenchBasic(unittest.TestCase):
    """Verify TestBench can be instantiated and run."""

    def test_testbench_runs(self):
        class _M(Module):
            def __init__(self):
                self.clk = Input(); self.d = Input(8); self.q = Output(8)
                self.r = Register(8)
                super().__init__()
                @self.comb
                def drive(): self.q = self.r
                @self.posedge(self.clk)
                def seq(): self.r = self.d

        from veripy.verify import initial

        class _TB(TestBench):
            def create_module(self): return _M()
            def test_passthrough(self):
                dut = self.dut; self.clock('clk', 10)
                @initial
                def _():
                    dut.d = 0xAB; yield 10
                    self.assertEqual(self.get('q'), 0xAB)

        suite = unittest.TestLoader().loadTestsFromName('test_passthrough', _TB)
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(result.errors + result.failures, [])


if __name__ == '__main__':
    unittest.main()
