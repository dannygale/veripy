"""Dual-path tests for the v1 decode port."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'examples'))

from decode_v1 import Decode, ControlUnit
from decode_v1 import (OP_NOP, OP_LOAD, OP_STORE, OP_MOV, OP_LI, OP_ADD,
    OP_SUB, OP_AND, OP_JMP, OP_BRCC, OP_HLT, OP_CALL, OP_RET, OP_ADDI,
    OP_CMP, OP_SUBI, OP_LUI, OP_IRET, OP_SRA,
    ALU_ADD, ALU_SUB, ALU_AND, ALU_PASS, ALU_LUI, ALU_SRA)
from veripy import VeripyTestCase


def instr(opcode, rd=0, rs1=0, rs2=0, imm=0):
    """Build a 16-bit instruction word."""
    return (opcode << 11) | (rd << 8) | (rs1 << 5) | (rs2 << 2) | (imm & 0xFF)


class TestControlUnit(VeripyTestCase):
    def create_module(self):
        return ControlUnit()

    def test_nop(self):
        @self.initial
        def stim():
            self.set(opcode=OP_NOP)
            yield 1
            self.assertEqual(self.out('reg_we'), 0)
            self.assertEqual(self.out('alu_op'), ALU_ADD)
        self.run_sim()

    def test_load(self):
        @self.initial
        def stim():
            self.set(opcode=OP_LOAD)
            yield 1
            self.assertEqual(self.out('reg_we'), 1)
            self.assertEqual(self.out('mem_re'), 1)
            self.assertEqual(self.out('mem_to_reg'), 1)
            self.assertEqual(self.out('use_imm'), 1)
        self.run_sim()

    def test_store(self):
        @self.initial
        def stim():
            self.set(opcode=OP_STORE)
            yield 1
            self.assertEqual(self.out('mem_we'), 1)
            self.assertEqual(self.out('reg_we'), 0)
        self.run_sim()

    def test_alu_ops(self):
        @self.initial
        def stim():
            self.set(opcode=OP_SUB)
            yield 1
            self.assertEqual(self.out('reg_we'), 1)
            self.assertEqual(self.out('alu_op'), ALU_SUB)
        self.run_sim()

    def test_li(self):
        @self.initial
        def stim():
            self.set(opcode=OP_LI)
            yield 1
            self.assertEqual(self.out('alu_op'), ALU_PASS)
            self.assertEqual(self.out('use_imm'), 1)
        self.run_sim()

    def test_jump(self):
        @self.initial
        def stim():
            self.set(opcode=OP_JMP)
            yield 1
            self.assertEqual(self.out('is_jump'), 1)
        self.run_sim()

    def test_brcc(self):
        @self.initial
        def stim():
            self.set(opcode=OP_BRCC)
            yield 1
            self.assertEqual(self.out('is_brcc'), 1)
        self.run_sim()

    def test_halt(self):
        @self.initial
        def stim():
            self.set(opcode=OP_HLT)
            yield 1
            self.assertEqual(self.out('is_halt'), 1)
        self.run_sim()

    def test_call(self):
        @self.initial
        def stim():
            self.set(opcode=OP_CALL)
            yield 1
            self.assertEqual(self.out('is_call'), 1)
        self.run_sim()

    def test_cmp(self):
        @self.initial
        def stim():
            self.set(opcode=OP_CMP)
            yield 1
            self.assertEqual(self.out('is_cmp'), 1)
            self.assertEqual(self.out('alu_op'), ALU_SUB)
            self.assertEqual(self.out('reg_we'), 0)
        self.run_sim()

    def test_lui(self):
        @self.initial
        def stim():
            self.set(opcode=OP_LUI)
            yield 1
            self.assertEqual(self.out('alu_op'), ALU_LUI)
        self.run_sim()

    def test_iret(self):
        @self.initial
        def stim():
            self.set(opcode=OP_IRET)
            yield 1
            self.assertEqual(self.out('is_iret'), 1)
        self.run_sim()


class TestDecode(VeripyTestCase):
    def create_module(self):
        return Decode()

    def test_field_extraction(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_ADD, rd=3, rs1=5, rs2=2))
            yield 1
            self.assertEqual(self.out('rd_addr'), 3)
            self.assertEqual(self.out('rs1_addr'), 5)
            self.assertEqual(self.out('rs2_addr'), 2)
            self.assertEqual(self.out('reg_we'), 1)
        self.run_sim()

    def test_rs1_remap_addi(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_ADDI, rd=3, rs1=7, imm=5))
            yield 1
            self.assertEqual(self.out('rs1_addr'), 3)
        self.run_sim()

    def test_rs1_remap_jmp(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_JMP, rd=5))
            yield 1
            self.assertEqual(self.out('rs1_addr'), 5)
        self.run_sim()

    def test_rs2_remap_store(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_STORE, rd=4, rs1=2, imm=10))
            yield 1
            self.assertEqual(self.out('rs2_addr'), 4)
        self.run_sim()

    def test_immediate_zero_extend(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_LI, rd=1, imm=0xAB))
            yield 1
            self.assertEqual(self.out('immediate'), 0x00AB)
        self.run_sim()

    def test_branch_offset_positive(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_BRCC, rd=0, imm=0x10))
            yield 1
            self.assertEqual(self.out('branch_offset'), 0x0010)
        self.run_sim()

    def test_branch_offset_negative(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_BRCC, rd=0, imm=0xFC))
            yield 1
            self.assertEqual(self.out('branch_offset'), 0xFFFC)
        self.run_sim()

    def test_branch_cond(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_BRCC, rd=5, imm=2))
            yield 1
            self.assertEqual(self.out('branch_cond'), 5)
        self.run_sim()

    def test_control_passthrough(self):
        @self.initial
        def stim():
            self.set(instr=instr(OP_LOAD, rd=1, rs1=2, imm=4))
            yield 1
            self.assertEqual(self.out('reg_we'), 1)
            self.assertEqual(self.out('mem_re'), 1)
            self.assertEqual(self.out('mem_to_reg'), 1)
            self.assertEqual(self.out('use_imm'), 1)
        self.run_sim()
