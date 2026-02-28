"""Tests for RegFile: simulation, Verilog emission, and dual-path."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.regfile import RegFile
from veripy import VeripyTestCase
from veripy.sim import SimEngine

T = 10


class TestRegFileSim(unittest.TestCase):
    def test_read_after_write(self):
        rf = RegFile(width=8, depth=4)
        sim = SimEngine(rf)
        sim.clock(rf.clock, T)
        @sim.initial
        def _():
            rf.we.set(1); rf.waddr.set(1); rf.wdata.set(42); yield T
            rf.we.set(0); rf.raddr1.set(1); yield T
            self.assertEqual(int(rf.rdata1), 42)
        sim.run()

    def test_r0_hardwired_zero(self):
        rf = RegFile(width=8, depth=4)
        sim = SimEngine(rf)
        sim.clock(rf.clock, T)
        @sim.initial
        def _():
            rf.we.set(1); rf.waddr.set(0); rf.wdata.set(0xFF); yield T
            rf.we.set(0); rf.raddr1.set(0); yield T
            self.assertEqual(int(rf.rdata1), 0)
        sim.run()

    def test_two_read_ports(self):
        rf = RegFile(width=8, depth=4)
        sim = SimEngine(rf)
        sim.clock(rf.clock, T)
        @sim.initial
        def _():
            rf.we.set(1); rf.waddr.set(1); rf.wdata.set(10); yield T
            rf.waddr.set(2); rf.wdata.set(20); yield T
            rf.we.set(0); rf.raddr1.set(1); rf.raddr2.set(2); yield T
            self.assertEqual(int(rf.rdata1), 10)
            self.assertEqual(int(rf.rdata2), 20)
        sim.run()

    def test_overwrite(self):
        rf = RegFile(width=8, depth=4)
        sim = SimEngine(rf)
        sim.clock(rf.clock, T)
        @sim.initial
        def _():
            rf.we.set(1); rf.waddr.set(3); rf.wdata.set(100); yield T
            rf.wdata.set(200); yield T
            rf.we.set(0); rf.raddr1.set(3); yield T
            self.assertEqual(int(rf.rdata1), 200)
        sim.run()


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


class TestRegFileDualPath(VeripyTestCase):
    def create_module(self):
        return RegFile(width=8, depth=4)

    def test_write_and_read(self):
        @self.always
        def clock():
            self.set(clock=0)
            yield 5
            self.set(clock=1)
            yield 5

        @self.initial
        def stim():
            self.set(we=1, waddr=1, wdata=42, raddr1=1, raddr2=1)
            yield 10
            self.set(we=0)
            yield 10
            self.assertEqual(self.out('rdata1'), 42)

        self.run_sim()

    def test_r0_write_ignored(self):
        @self.always
        def clock():
            self.set(clock=0)
            yield 5
            self.set(clock=1)
            yield 5

        @self.initial
        def stim():
            self.set(we=1, waddr=0, wdata=0xFF, raddr1=0, raddr2=0)
            yield 10
            self.set(we=0)
            yield 10
            self.assertEqual(self.out('rdata1'), 0)

        self.run_sim()

    def test_two_ports_independent(self):
        @self.always
        def clock():
            self.set(clock=0)
            yield 5
            self.set(clock=1)
            yield 5

        @self.initial
        def stim():
            self.set(we=1, waddr=1, wdata=10, raddr1=1, raddr2=1)
            yield 10
            self.set(waddr=2, wdata=20, raddr1=1, raddr2=2)
            yield 10
            self.set(we=0)
            yield 10
            self.assertEqual(self.out('rdata1'), 10)
            self.assertEqual(self.out('rdata2'), 20)

        self.run_sim()


if __name__ == '__main__':
    unittest.main()
