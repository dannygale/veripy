"""Tests for PipeReg and ForwardMux: simulation + Verilog output."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.pipe_stage import PipeReg, ForwardMux


class TestPipeRegSim(unittest.TestCase):
    def setUp(self):
        self.pr = PipeReg(width=8)

    def _reset(self):
        self.pr.reset._val = 1
        self.pr.tick()
        self.pr.reset._val = 0

    def test_reset_clears(self):
        self.pr.d._val = 0xFF
        self.pr.tick()
        self._reset()
        self.assertEqual(self.pr.q._val, 0)

    def test_captures_data(self):
        self._reset()
        self.pr.d._val = 0xAB
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0xAB)

    def test_stall_freezes(self):
        self._reset()
        self.pr.d._val = 0x11
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0x11)
        self.pr.stall._val = 1
        self.pr.d._val = 0x22
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0x11)  # frozen

    def test_stall_release(self):
        self._reset()
        self.pr.d._val = 0x11
        self.pr.stall._val = 1
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0)  # stalled, still 0
        self.pr.stall._val = 0
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0x11)

    def test_flush_clears(self):
        self._reset()
        self.pr.d._val = 0xCC
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0xCC)
        self.pr.flush._val = 1
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0)
        self.pr.flush._val = 0

    def test_flush_priority_over_data(self):
        self._reset()
        self.pr.d._val = 0xFF
        self.pr.flush._val = 1
        self.pr.tick()
        self.assertEqual(self.pr.q._val, 0)  # flush wins


class TestForwardMuxSim(unittest.TestCase):
    def setUp(self):
        self.fm = ForwardMux(width=8)
        self.fm.reg_val._val = 0x10
        self.fm.ex_val._val = 0xEE
        self.fm.mem_val._val = 0xDD
        self.fm.rs_addr._val = 3

    def test_no_forward(self):
        self.fm.ex_rd_addr._val = 5
        self.fm.mem_rd_addr._val = 5
        self.fm.tick()
        self.assertEqual(self.fm.out._val, 0x10)
        self.assertEqual(self.fm.fwd_sel._val, 0)

    def test_ex_forward(self):
        self.fm.ex_we._val = 1
        self.fm.ex_rd_addr._val = 3
        self.fm.tick()
        self.assertEqual(self.fm.out._val, 0xEE)
        self.assertEqual(self.fm.fwd_sel._val, 1)

    def test_mem_forward(self):
        self.fm.mem_we._val = 1
        self.fm.mem_rd_addr._val = 3
        self.fm.tick()
        self.assertEqual(self.fm.out._val, 0xDD)
        self.assertEqual(self.fm.fwd_sel._val, 2)

    def test_ex_priority_over_mem(self):
        self.fm.ex_we._val = 1
        self.fm.mem_we._val = 1
        self.fm.ex_rd_addr._val = 3
        self.fm.mem_rd_addr._val = 3
        self.fm.tick()
        self.assertEqual(self.fm.out._val, 0xEE)  # EX wins
        self.assertEqual(self.fm.fwd_sel._val, 1)

    def test_match_without_we(self):
        self.fm.ex_rd_addr._val = 3
        self.fm.mem_rd_addr._val = 3
        # Both match but neither has write-enable
        self.fm.tick()
        self.assertEqual(self.fm.out._val, 0x10)  # no forward
        self.assertEqual(self.fm.fwd_sel._val, 0)


class TestPipeRegVerilog(unittest.TestCase):
    def setUp(self):
        self.v = PipeReg(width=16).to_verilog(module_name='pipe_reg')

    def test_module_name(self):
        self.assertIn('module pipe_reg', self.v)

    def test_ports(self):
        self.assertIn('input clock', self.v)
        self.assertIn('input reset', self.v)
        self.assertIn('input flush', self.v)
        self.assertIn('input stall', self.v)
        self.assertIn('input [15:0] d', self.v)
        self.assertIn('output reg [15:0] q', self.v)

    def test_posedge_block(self):
        self.assertIn('always @(posedge clock)', self.v)

    def test_flush_reset(self):
        self.assertIn('if (reset || flush)', self.v)

    def test_stall_gate(self):
        self.assertIn('if (!stall)', self.v)


class TestForwardMuxVerilog(unittest.TestCase):
    def setUp(self):
        self.v = ForwardMux(width=16).to_verilog(module_name='forward_mux')

    def test_always_star(self):
        self.assertIn('always @(*)', self.v)

    def test_blocking_assign(self):
        # Comb blocks should use = not <=
        self.assertIn('out = ex_val;', self.v)
        self.assertIn('out = mem_val;', self.v)
        self.assertIn('out = reg_val;', self.v)

    def test_priority_structure(self):
        # EX check should come before MEM check
        ex_pos = self.v.index('ex_we')
        mem_pos = self.v.index('mem_we')
        self.assertLess(ex_pos, mem_pos)


if __name__ == '__main__':
    unittest.main()
