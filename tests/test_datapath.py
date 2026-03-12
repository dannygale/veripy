"""Tests for module instantiation: sub-modules with port wiring."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.datapath import ALU, Datapath
from veripy.verify import TestBench, initial

T = 10


class TestALUSim(unittest.TestCase):
    """ALU is purely combinational — just settle, no clock needed."""

    def test_add(self):
        alu = ALU(width=8)
        alu.a.set(10); alu.b.set(20); alu.op.set(0)
        alu._settle_comb()
        self.assertEqual(int(alu.result), 30)

    def test_sub(self):
        alu = ALU(width=8)
        alu.a.set(50); alu.b.set(20); alu.op.set(1)
        alu._settle_comb()
        self.assertEqual(int(alu.result), 30)

    def test_default_zero(self):
        alu = ALU(width=8)
        alu.a.set(99); alu.b.set(99); alu.op.set(15)
        alu._settle_comb()
        self.assertEqual(int(alu.result), 0)


class TestDatapathSim(TestBench):
    def create_module(self): return Datapath(width=8)

    def test_alu_wiring(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 7; dut.b = 3; dut.op = 0; yield T
            self.assertEqual(self.get('result'), 10)

    def test_pipeline_delay(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 10; dut.b = 5; dut.op = 0; yield T
            self.assertEqual(self.get('result'), 15)
            dut.a = 100; dut.b = 1; dut.op = 0; yield T
            self.assertEqual(self.get('result'), 101)

    def test_reset_clears_piped(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 10; dut.b = 5; dut.op = 0; yield T
            self.assertNotEqual(self.get('result'), 0)
            dut.reset = 1; yield T
            self.assertEqual(self.get('result'), 0)

    def test_sub_through_pipeline(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.a = 50; dut.b = 8; dut.op = 1; yield T
            self.assertEqual(self.get('result'), 42)


class TestDatapathVerilog(unittest.TestCase):
    def setUp(self):
        self.v = Datapath(width=16).to_verilog()

    def test_submodule_instance(self):
        self.assertIn('alu #(.width(16)) alu', self.v)

    def test_port_map(self):
        self.assertIn('.a(alu_a)', self.v)
        self.assertIn('.b(alu_b)', self.v)
        self.assertIn('.op(alu_op)', self.v)
        self.assertIn('.result(alu_result)', self.v)

    def test_wire_declarations(self):
        self.assertIn('wire [15:0] alu_a;', self.v)
        self.assertIn('wire [15:0] alu_b;', self.v)
        self.assertIn('wire [15:0] alu_result;', self.v)
        self.assertIn('wire [3:0] alu_op;', self.v)

    def test_wiring_assigns(self):
        self.assertIn('assign alu_a = a;', self.v)
        self.assertIn('assign alu_b = b;', self.v)
        self.assertIn('assign alu_op = op;', self.v)

    def test_piped_reads_alu(self):
        self.assertIn('result <= alu_result;', self.v)

    def test_no_alu_internals_in_parent(self):
        self.assertNotIn('(a + b)', self.v)


class TestEmitAll(unittest.TestCase):
    def test_hierarchy_compiles(self):
        d = Datapath(width=8)
        top = d.to_verilog()
        sub = d.alu.to_verilog()
        self.assertIn('module alu', sub)
        self.assertIn('module datapath', top)
        import subprocess, tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "check.v")
            out = os.path.join(tmpdir, "check.out")
            with open(src, "w") as f:
                f.write(sub + '\n\n' + top)
            r = subprocess.run(["iverilog", "-o", out, src], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
