"""Tests for the v1 decode port."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'examples'))

from decode_v1 import Decode, ControlUnit
from decode_v1 import (OP_NOP, OP_LOAD, OP_STORE, OP_MOV, OP_LI, OP_ADD,
    OP_SUB, OP_AND, OP_JMP, OP_BRCC, OP_HLT, OP_CALL, OP_RET, OP_ADDI,
    OP_CMP, OP_SUBI, OP_LUI, OP_IRET, OP_SRA,
    ALU_ADD, ALU_SUB, ALU_AND, ALU_PASS, ALU_LUI, ALU_SRA)
from veripy.verify import TestBench, initial


def instr(opcode, rd=0, rs1=0, rs2=0, imm=0):
    return (opcode << 11) | (rd << 8) | (rs1 << 5) | (rs2 << 2) | (imm & 0xFF)


class TestControlUnit(TestBench):
    def create_module(self): return ControlUnit()

    def test_nop(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_NOP; yield 1
            self.assertEqual(self.get('reg_we'), 0)
            self.assertEqual(self.get('alu_op'), ALU_ADD)

    def test_load(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_LOAD; yield 1
            self.assertEqual(self.get('reg_we'), 1)
            self.assertEqual(self.get('mem_re'), 1)
            self.assertEqual(self.get('mem_to_reg'), 1)
            self.assertEqual(self.get('use_imm'), 1)

    def test_store(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_STORE; yield 1
            self.assertEqual(self.get('mem_we'), 1)
            self.assertEqual(self.get('reg_we'), 0)

    def test_alu_ops(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_SUB; yield 1
            self.assertEqual(self.get('reg_we'), 1)
            self.assertEqual(self.get('alu_op'), ALU_SUB)

    def test_li(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_LI; yield 1
            self.assertEqual(self.get('alu_op'), ALU_PASS)
            self.assertEqual(self.get('use_imm'), 1)

    def test_jump(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_JMP; yield 1
            self.assertEqual(self.get('is_jump'), 1)

    def test_brcc(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_BRCC; yield 1
            self.assertEqual(self.get('is_brcc'), 1)

    def test_halt(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_HLT; yield 1
            self.assertEqual(self.get('is_halt'), 1)

    def test_call(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_CALL; yield 1
            self.assertEqual(self.get('is_call'), 1)

    def test_cmp(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_CMP; yield 1
            self.assertEqual(self.get('is_cmp'), 1)
            self.assertEqual(self.get('alu_op'), ALU_SUB)
            self.assertEqual(self.get('reg_we'), 0)

    def test_lui(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_LUI; yield 1
            self.assertEqual(self.get('alu_op'), ALU_LUI)

    def test_iret(self):
        dut = self.dut
        @initial
        def _():
            dut.opcode = OP_IRET; yield 1
            self.assertEqual(self.get('is_iret'), 1)


class TestDecode(TestBench):
    def create_module(self): return Decode()

    def test_field_extraction(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_ADD, rd=3, rs1=5, rs2=2); yield 1
            self.assertEqual(self.get('rd_addr'), 3)
            self.assertEqual(self.get('rs1_addr'), 5)
            self.assertEqual(self.get('rs2_addr'), 2)
            self.assertEqual(self.get('reg_we'), 1)

    def test_rs1_remap_addi(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_ADDI, rd=3, rs1=7, imm=5); yield 1
            self.assertEqual(self.get('rs1_addr'), 3)

    def test_rs1_remap_jmp(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_JMP, rd=5); yield 1
            self.assertEqual(self.get('rs1_addr'), 5)

    def test_rs2_remap_store(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_STORE, rd=4, rs1=2, imm=10); yield 1
            self.assertEqual(self.get('rs2_addr'), 4)

    def test_immediate_zero_extend(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_LI, rd=1, imm=0xAB); yield 1
            self.assertEqual(self.get('immediate'), 0x00AB)

    def test_branch_offset_positive(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_BRCC, rd=0, imm=0x10); yield 1
            self.assertEqual(self.get('branch_offset'), 0x0010)

    def test_branch_offset_negative(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_BRCC, rd=0, imm=0xFC); yield 1
            self.assertEqual(self.get('branch_offset'), 0xFFFC)

    def test_branch_cond(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_BRCC, rd=5, imm=2); yield 1
            self.assertEqual(self.get('branch_cond'), 5)

    def test_control_passthrough(self):
        dut = self.dut
        @initial
        def _():
            dut.instr = instr(OP_LOAD, rd=1, rs1=2, imm=4); yield 1
            self.assertEqual(self.get('reg_we'), 1)
            self.assertEqual(self.get('mem_re'), 1)
            self.assertEqual(self.get('mem_to_reg'), 1)
            self.assertEqual(self.get('use_imm'), 1)
