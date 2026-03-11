"""Tests for the v1 ALU port."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'examples'))

from alu_v1 import ALU, ALU_ADD, ALU_SUB, ALU_AND, ALU_OR, ALU_XOR, ALU_NOT
from alu_v1 import ALU_SHL, ALU_SHR, ALU_SRA, ALU_PASS, ALU_LUI
from veripy.verify import TestBench, initial


class TestALU(TestBench):
    def create_module(self):
        return ALU(16)

    def test_add(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 10; dut.b = 20; dut.op = ALU_ADD
            yield 1
            self.assertEqual(self.get('result'), 30)
            self.assertEqual(self.get('zero'), 0)

    def test_add_carry(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0xFFFF; dut.b = 1; dut.op = ALU_ADD
            yield 1
            self.assertEqual(self.get('result'), 0)
            self.assertEqual(self.get('carry'), 1)
            self.assertEqual(self.get('zero'), 1)

    def test_sub(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 30; dut.b = 30; dut.op = ALU_SUB
            yield 1
            self.assertEqual(self.get('result'), 0)
            self.assertEqual(self.get('zero'), 1)

    def test_sub_borrow(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 10; dut.b = 20; dut.op = ALU_SUB
            yield 1
            self.assertEqual(self.get('carry'), 1)

    def test_and(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0xFFFF; dut.b = 0x00FF; dut.op = ALU_AND
            yield 1
            self.assertEqual(self.get('result'), 0x00FF)

    def test_or(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0xA000; dut.b = 0x0005; dut.op = ALU_OR
            yield 1
            self.assertEqual(self.get('result'), 0xA005)

    def test_xor(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0xFFFF; dut.b = 0xFFFF; dut.op = ALU_XOR
            yield 1
            self.assertEqual(self.get('result'), 0)

    def test_not(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0; dut.b = 0; dut.op = ALU_NOT
            yield 1
            self.assertEqual(self.get('result'), 0xFFFF)

    def test_shl(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 1; dut.b = 0; dut.op = ALU_SHL
            yield 1
            self.assertEqual(self.get('result'), 2)
            self.assertEqual(self.get('carry'), 0)

    def test_shl_carry(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0x8001; dut.b = 0; dut.op = ALU_SHL
            yield 1
            self.assertEqual(self.get('result'), 2)
            self.assertEqual(self.get('carry'), 1)

    def test_shr(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0x8000; dut.b = 0; dut.op = ALU_SHR
            yield 1
            self.assertEqual(self.get('result'), 0x4000)

    def test_sra_pos(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0x0080; dut.b = 0; dut.op = ALU_SRA
            yield 1
            self.assertEqual(self.get('result'), 0x0040)

    def test_sra_neg(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0x8000; dut.b = 0; dut.op = ALU_SRA
            yield 1
            self.assertEqual(self.get('result'), 0xC000)
            self.assertEqual(self.get('sign'), 1)

    def test_pass(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0; dut.b = 0x1234; dut.op = ALU_PASS
            yield 1
            self.assertEqual(self.get('result'), 0x1234)

    def test_lui(self):
        dut = self.dut
        @initial
        def _():
            dut.a = 0; dut.b = 0x00AB; dut.op = ALU_LUI
            yield 1
            self.assertEqual(self.get('result'), 0xAB00)
