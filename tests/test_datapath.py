"""Tests for module instantiation: sub-modules with port wiring."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.datapath import ALU, Datapath
from veripy.sim import SimEngine

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


class TestDatapathSim(unittest.TestCase):
    def test_alu_wiring(self):
        d = Datapath(width=8)
        sim = SimEngine(d)
        sim.clock(d.clock, T)
        @sim.initial
        def _():
            d.reset.set(1); yield T; d.reset.set(0)
            d.a.set(7); d.b.set(3); d.op.set(0)
            yield T
            self.assertEqual(int(d.alu.result), 10)
        sim.run()

    def test_pipeline_delay(self):
        d = Datapath(width=8)
        sim = SimEngine(d)
        sim.clock(d.clock, T)
        @sim.initial
        def _():
            d.reset.set(1); yield T; d.reset.set(0)
            d.a.set(10); d.b.set(5); d.op.set(0)
            yield T
            self.assertEqual(int(d.piped), 15)
            d.a.set(100); d.b.set(1); d.op.set(0)
            yield T
            self.assertEqual(int(d.piped), 101)
        sim.run()

    def test_reset_clears_piped(self):
        d = Datapath(width=8)
        sim = SimEngine(d)
        sim.clock(d.clock, T)
        @sim.initial
        def _():
            d.reset.set(1); yield T; d.reset.set(0)
            d.a.set(10); d.b.set(5); d.op.set(0)
            yield T
            self.assertNotEqual(int(d.piped), 0)
            d.reset.set(1); yield T
            self.assertEqual(int(d.piped), 0)
        sim.run()

    def test_output_tracks_piped(self):
        d = Datapath(width=8)
        sim = SimEngine(d)
        sim.clock(d.clock, T)
        @sim.initial
        def _():
            d.reset.set(1); yield T; d.reset.set(0)
            d.a.set(20); d.b.set(3); d.op.set(1)
            yield T
            self.assertEqual(int(d.result), int(d.piped))
        sim.run()

    def test_sub_through_pipeline(self):
        d = Datapath(width=8)
        sim = SimEngine(d)
        sim.clock(d.clock, T)
        @sim.initial
        def _():
            d.reset.set(1); yield T; d.reset.set(0)
            d.a.set(50); d.b.set(8); d.op.set(1)
            yield T
            self.assertEqual(int(d.piped), 42)
        sim.run()


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
        self.assertIn('piped <= alu_result;', self.v)

    def test_no_alu_internals_in_parent(self):
        self.assertNotIn('(a + b)', self.v)


class TestEmitAll(unittest.TestCase):
    def test_emit_all_includes_both(self):
        from veripy.emit_verilog import VerilogEmitter
        d = Datapath(width=8)
        full = VerilogEmitter(d).emit_all()
        self.assertIn('module alu', full)
        self.assertIn('module datapath', full)
        self.assertLess(full.index('module alu'), full.index('module datapath'))


if __name__ == '__main__':
    unittest.main()
