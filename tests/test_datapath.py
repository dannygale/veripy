"""Tests for module instantiation: sub-modules with port wiring."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.datapath import ALU, Datapath


class TestALUSim(unittest.TestCase):
    def setUp(self):
        self.alu = ALU(width=8)

    def test_add(self):
        self.alu.a._val = 10; self.alu.b._val = 20; self.alu.op._val = 0
        self.alu.tick()
        self.assertEqual(self.alu.result._val, 30)

    def test_sub(self):
        self.alu.a._val = 50; self.alu.b._val = 20; self.alu.op._val = 1
        self.alu.tick()
        self.assertEqual(self.alu.result._val, 30)

    def test_default_zero(self):
        self.alu.a._val = 99; self.alu.b._val = 99; self.alu.op._val = 15
        self.alu.tick()
        self.assertEqual(self.alu.result._val, 0)


class TestDatapathSim(unittest.TestCase):
    def setUp(self):
        self.d = Datapath(width=8)
        self.d.reset._val = 1; self.d.tick(); self.d.reset._val = 0

    def test_alu_wiring(self):
        """Parent inputs propagate to child ALU."""
        self.d.a._val = 7; self.d.b._val = 3; self.d.op._val = 0
        self.d.tick()
        self.assertEqual(self.d.alu.result._val, 10)

    def test_pipeline_delay(self):
        """Result appears one cycle after ALU computes."""
        self.d.a._val = 10; self.d.b._val = 5; self.d.op._val = 0
        self.d.tick()
        # ALU computed 15 this cycle, piped captures it
        self.assertEqual(self.d.piped._val, 15)
        # Change inputs — piped still holds previous result until next tick
        self.d.a._val = 100; self.d.b._val = 1; self.d.op._val = 0
        alu_before_tick = self.d.alu.result._val  # still 15 from last settle
        self.d.tick()
        self.assertEqual(self.d.piped._val, 101)

    def test_reset_clears_piped(self):
        self.d.a._val = 10; self.d.b._val = 5; self.d.op._val = 0
        self.d.tick()
        self.assertNotEqual(self.d.piped._val, 0)
        self.d.reset._val = 1; self.d.tick()
        self.assertEqual(self.d.piped._val, 0)

    def test_output_tracks_piped(self):
        self.d.a._val = 20; self.d.b._val = 3; self.d.op._val = 1
        self.d.tick()
        self.assertEqual(self.d.result._val, self.d.piped._val)

    def test_sub_through_pipeline(self):
        self.d.a._val = 50; self.d.b._val = 8; self.d.op._val = 1
        self.d.tick()
        self.assertEqual(self.d.piped._val, 42)


class TestDatapathVerilog(unittest.TestCase):
    def setUp(self):
        self.v = Datapath(width=16).to_verilog()

    def test_submodule_instance(self):
        self.assertIn('alu alu', self.v)

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
        """Parent should not contain ALU's always @(*) block."""
        # The ALU's comb logic lives in the ALU module, not the parent
        self.assertNotIn('(a + b)', self.v)


class TestEmitAll(unittest.TestCase):
    def test_emit_all_includes_both(self):
        from veripy.emit_verilog import VerilogEmitter
        d = Datapath(width=8)
        full = VerilogEmitter(d).emit_all()
        self.assertIn('module alu', full)
        self.assertIn('module datapath', full)
        # ALU definition should come before datapath
        self.assertLess(full.index('module alu'), full.index('module datapath'))


if __name__ == '__main__':
    unittest.main()
