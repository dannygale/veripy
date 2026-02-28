"""Tests for PipeReg and ForwardMux: simulation + Verilog output."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.pipe_stage import PipeReg, ForwardMux
from veripy.sim import SimEngine

T = 10


class TestPipeRegSim(unittest.TestCase):
    def test_reset_clears(self):
        pr = PipeReg(width=8)
        sim = SimEngine(pr)
        sim.clock(pr.clock, T)
        @sim.initial
        def _():
            pr.d.set(0xFF); yield T
            pr.reset.set(1); yield T
            self.assertEqual(int(pr.q), 0)
        sim.run()

    def test_captures_data(self):
        pr = PipeReg(width=8)
        sim = SimEngine(pr)
        sim.clock(pr.clock, T)
        @sim.initial
        def _():
            pr.reset.set(1); yield T; pr.reset.set(0)
            pr.d.set(0xAB); yield T
            self.assertEqual(int(pr.q), 0xAB)
        sim.run()

    def test_stall_freezes(self):
        pr = PipeReg(width=8)
        sim = SimEngine(pr)
        sim.clock(pr.clock, T)
        @sim.initial
        def _():
            pr.reset.set(1); yield T; pr.reset.set(0)
            pr.d.set(0x11); yield T
            self.assertEqual(int(pr.q), 0x11)
            pr.stall.set(1); pr.d.set(0x22); yield T
            self.assertEqual(int(pr.q), 0x11)  # frozen
        sim.run()

    def test_stall_release(self):
        pr = PipeReg(width=8)
        sim = SimEngine(pr)
        sim.clock(pr.clock, T)
        @sim.initial
        def _():
            pr.reset.set(1); yield T; pr.reset.set(0)
            pr.d.set(0x11); pr.stall.set(1); yield T
            self.assertEqual(int(pr.q), 0)  # stalled
            pr.stall.set(0); yield T
            self.assertEqual(int(pr.q), 0x11)
        sim.run()

    def test_flush_clears(self):
        pr = PipeReg(width=8)
        sim = SimEngine(pr)
        sim.clock(pr.clock, T)
        @sim.initial
        def _():
            pr.reset.set(1); yield T; pr.reset.set(0)
            pr.d.set(0xCC); yield T
            self.assertEqual(int(pr.q), 0xCC)
            pr.flush.set(1); yield T
            self.assertEqual(int(pr.q), 0)
        sim.run()

    def test_flush_priority_over_data(self):
        pr = PipeReg(width=8)
        sim = SimEngine(pr)
        sim.clock(pr.clock, T)
        @sim.initial
        def _():
            pr.reset.set(1); yield T; pr.reset.set(0)
            pr.d.set(0xFF); pr.flush.set(1); yield T
            self.assertEqual(int(pr.q), 0)  # flush wins
        sim.run()


class TestForwardMuxSim(unittest.TestCase):
    """ForwardMux is purely combinational."""

    def _make(self):
        fm = ForwardMux(width=8)
        fm.reg_val.set(0x10)
        fm.ex_val.set(0xEE)
        fm.mem_val.set(0xDD)
        fm.rs_addr.set(3)
        return fm

    def test_no_forward(self):
        fm = self._make()
        fm.ex_rd_addr.set(5); fm.mem_rd_addr.set(5)
        fm._settle_comb()
        self.assertEqual(int(fm.out), 0x10)
        self.assertEqual(int(fm.fwd_sel), 0)

    def test_ex_forward(self):
        fm = self._make()
        fm.ex_we.set(1); fm.ex_rd_addr.set(3)
        fm._settle_comb()
        self.assertEqual(int(fm.out), 0xEE)
        self.assertEqual(int(fm.fwd_sel), 1)

    def test_mem_forward(self):
        fm = self._make()
        fm.mem_we.set(1); fm.mem_rd_addr.set(3)
        fm._settle_comb()
        self.assertEqual(int(fm.out), 0xDD)
        self.assertEqual(int(fm.fwd_sel), 2)

    def test_ex_priority_over_mem(self):
        fm = self._make()
        fm.ex_we.set(1); fm.mem_we.set(1)
        fm.ex_rd_addr.set(3); fm.mem_rd_addr.set(3)
        fm._settle_comb()
        self.assertEqual(int(fm.out), 0xEE)  # EX wins
        self.assertEqual(int(fm.fwd_sel), 1)

    def test_match_without_we(self):
        fm = self._make()
        fm.ex_rd_addr.set(3); fm.mem_rd_addr.set(3)
        fm._settle_comb()
        self.assertEqual(int(fm.out), 0x10)  # no forward
        self.assertEqual(int(fm.fwd_sel), 0)


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
        self.assertIn('output [15:0] q', self.v)

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
        self.assertIn('out = ex_val;', self.v)
        self.assertIn('out = mem_val;', self.v)
        self.assertIn('out = reg_val;', self.v)

    def test_priority_structure(self):
        ex_pos = self.v.index('ex_we')
        mem_pos = self.v.index('mem_we')
        self.assertLess(ex_pos, mem_pos)


if __name__ == '__main__':
    unittest.main()
