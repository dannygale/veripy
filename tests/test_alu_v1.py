"""Dual-path tests for the v1 ALU port."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'examples'))

from alu_v1 import ALU, ALU_ADD, ALU_SUB, ALU_AND, ALU_OR, ALU_XOR, ALU_NOT
from alu_v1 import ALU_SHL, ALU_SHR, ALU_SRA, ALU_PASS, ALU_LUI
from veripy import VeripyTestCase


class TestALU(VeripyTestCase):
    def create_module(self):
        return ALU(16)

    def test_add(self):
        @self.initial
        def stim():
            self.set(a=10, b=20, op=ALU_ADD)
            yield 1
            self.assertEqual(self.out('result'), 30)
            self.assertEqual(self.out('zero'), 0)
        self.run_sim()

    def test_add_carry(self):
        @self.initial
        def stim():
            self.set(a=0xFFFF, b=1, op=ALU_ADD)
            yield 1
            self.assertEqual(self.out('result'), 0)
            self.assertEqual(self.out('carry'), 1)
            self.assertEqual(self.out('zero'), 1)
        self.run_sim()

    def test_sub(self):
        @self.initial
        def stim():
            self.set(a=30, b=30, op=ALU_SUB)
            yield 1
            self.assertEqual(self.out('result'), 0)
            self.assertEqual(self.out('zero'), 1)
        self.run_sim()

    def test_sub_borrow(self):
        @self.initial
        def stim():
            self.set(a=10, b=20, op=ALU_SUB)
            yield 1
            self.assertEqual(self.out('carry'), 1)
        self.run_sim()

    def test_and(self):
        @self.initial
        def stim():
            self.set(a=0xFFFF, b=0x00FF, op=ALU_AND)
            yield 1
            self.assertEqual(self.out('result'), 0x00FF)
        self.run_sim()

    def test_or(self):
        @self.initial
        def stim():
            self.set(a=0xA000, b=0x0005, op=ALU_OR)
            yield 1
            self.assertEqual(self.out('result'), 0xA005)
        self.run_sim()

    def test_xor(self):
        @self.initial
        def stim():
            self.set(a=0xFFFF, b=0xFFFF, op=ALU_XOR)
            yield 1
            self.assertEqual(self.out('result'), 0)
        self.run_sim()

    def test_not(self):
        @self.initial
        def stim():
            self.set(a=0, b=0, op=ALU_NOT)
            yield 1
            self.assertEqual(self.out('result'), 0xFFFF)
        self.run_sim()

    def test_shl(self):
        @self.initial
        def stim():
            self.set(a=1, b=0, op=ALU_SHL)
            yield 1
            self.assertEqual(self.out('result'), 2)
            self.assertEqual(self.out('carry'), 0)
        self.run_sim()

    def test_shl_carry(self):
        @self.initial
        def stim():
            self.set(a=0x8001, b=0, op=ALU_SHL)
            yield 1
            self.assertEqual(self.out('result'), 2)
            self.assertEqual(self.out('carry'), 1)
        self.run_sim()

    def test_shr(self):
        @self.initial
        def stim():
            self.set(a=0x8000, b=0, op=ALU_SHR)
            yield 1
            self.assertEqual(self.out('result'), 0x4000)
        self.run_sim()

    def test_sra_pos(self):
        @self.initial
        def stim():
            self.set(a=0x0080, b=0, op=ALU_SRA)
            yield 1
            self.assertEqual(self.out('result'), 0x0040)
        self.run_sim()

    def test_sra_neg(self):
        @self.initial
        def stim():
            self.set(a=0x8000, b=0, op=ALU_SRA)
            yield 1
            self.assertEqual(self.out('result'), 0xC000)
            self.assertEqual(self.out('sign'), 1)
        self.run_sim()

    def test_pass(self):
        @self.initial
        def stim():
            self.set(a=0, b=0x1234, op=ALU_PASS)
            yield 1
            self.assertEqual(self.out('result'), 0x1234)
        self.run_sim()

    def test_lui(self):
        @self.initial
        def stim():
            self.set(a=0, b=0x00AB, op=ALU_LUI)
            yield 1
            self.assertEqual(self.out('result'), 0xAB00)
        self.run_sim()
