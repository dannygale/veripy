"""Tests for RegFile: simulation, Verilog emission."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.regfile import RegFile
from veripy.verify import TestBench, initial

T = 10


class TestRegFileSim(TestBench):
    def create_module(self): return RegFile(width=8, depth=4)

    def test_read_after_write(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.we = 1; dut.waddr = 1; dut.wdata = 42; yield T
            dut.we = 0; dut.raddr1 = 1; yield T
            self.assertEqual(self.get('rdata1'), 42)

    def test_r0_hardwired_zero(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.we = 1; dut.waddr = 0; dut.wdata = 0xFF; yield T
            dut.we = 0; dut.raddr1 = 0; yield T
            self.assertEqual(self.get('rdata1'), 0)

    def test_two_read_ports(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.we = 1; dut.waddr = 1; dut.wdata = 10; yield T
            dut.waddr = 2; dut.wdata = 20; yield T
            dut.we = 0; dut.raddr1 = 1; dut.raddr2 = 2; yield T
            self.assertEqual(self.get('rdata1'), 10)
            self.assertEqual(self.get('rdata2'), 20)

    def test_overwrite(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.we = 1; dut.waddr = 3; dut.wdata = 100; yield T
            dut.wdata = 200; yield T
            dut.we = 0; dut.raddr1 = 3; yield T
            self.assertEqual(self.get('rdata1'), 200)


class TestRegFileVerilog(unittest.TestCase):
    def setUp(self):
        self.v = RegFile(width=8, depth=4).to_verilog()

    def test_mem_declaration(self):
        self.assertIn('reg [7:0] regs [0:3]', self.v)

    def test_read_assigns(self):
        self.assertIn('assign rdata1 = regs[raddr1]', self.v)
        self.assertIn('assign rdata2 = regs[raddr2]', self.v)

    def test_write_in_posedge(self):
        self.assertIn('always @(posedge clock)', self.v)
        self.assertIn('regs[waddr] <= wdata', self.v)

    def test_r0_guard(self):
        self.assertIn('if (we && waddr)', self.v)


if __name__ == '__main__':
    unittest.main()
