"""Tests for RegFile: simulation, Verilog emission, and dual-path."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from examples.regfile import RegFile
from veripy import VeripyTestCase


class TestRegFileSim(unittest.TestCase):
    def setUp(self):
        self.rf = RegFile(width=8, depth=4)

    def _write(self, addr, data):
        self.rf.we._val = 1
        self.rf.waddr._val = addr
        self.rf.wdata._val = data
        self.rf.tick()
        self.rf.we._val = 0

    def test_read_after_write(self):
        self._write(1, 42)
        self.rf.raddr1._val = 1
        self.rf.tick()
        self.assertEqual(self.rf.rdata1._val, 42)

    def test_r0_hardwired_zero(self):
        self._write(0, 0xFF)
        self.rf.raddr1._val = 0
        self.rf.tick()
        self.assertEqual(self.rf.rdata1._val, 0)

    def test_two_read_ports(self):
        self._write(1, 10)
        self._write(2, 20)
        self.rf.raddr1._val = 1
        self.rf.raddr2._val = 2
        self.rf.tick()
        self.assertEqual(self.rf.rdata1._val, 10)
        self.assertEqual(self.rf.rdata2._val, 20)

    def test_overwrite(self):
        self._write(3, 100)
        self._write(3, 200)
        self.rf.raddr1._val = 3
        self.rf.tick()
        self.assertEqual(self.rf.rdata1._val, 200)


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
        # Write r1, then read it back
        self.set(we=1, waddr=1, wdata=42, raddr1=1, raddr2=1)
        self.tick()
        self.set(we=0)
        self.tick()
        self.assertEqual(self.out('rdata1'), 42)

    def test_r0_write_ignored(self):
        # Write to r1, then attempt write to r0, verify r1 unchanged
        self.set(we=1, waddr=1, wdata=99, raddr1=1, raddr2=1)
        self.tick()
        self.set(waddr=0, wdata=0xFF)
        self.tick()
        self.set(we=0, raddr1=1)
        self.tick()
        self.assertEqual(self.out('rdata1'), 99)

    def test_two_ports_independent(self):
        self.set(we=1, waddr=1, wdata=10, raddr1=1, raddr2=1)
        self.tick()
        self.set(waddr=2, wdata=20, raddr1=1, raddr2=2)
        self.tick()
        self.set(we=0)
        self.tick()
        self.assertEqual(self.out('rdata1'), 10)
        self.assertEqual(self.out('rdata2'), 20)


if __name__ == '__main__':
    unittest.main()
