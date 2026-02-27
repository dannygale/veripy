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
        self.set(opcode=OP_NOP)
        self.tick()
        self.assertEqual(self.out('reg_we'), 0)
        self.assertEqual(self.out('alu_op'), ALU_ADD)

    def test_load(self):
        self.set(opcode=OP_LOAD)
        self.tick()
        self.assertEqual(self.out('reg_we'), 1)
        self.assertEqual(self.out('mem_re'), 1)
        self.assertEqual(self.out('mem_to_reg'), 1)
        self.assertEqual(self.out('use_imm'), 1)

    def test_store(self):
        self.set(opcode=OP_STORE)
        self.tick()
        self.assertEqual(self.out('mem_we'), 1)
        self.assertEqual(self.out('reg_we'), 0)

    def test_alu_ops(self):
        self.set(opcode=OP_SUB)
        self.tick()
        self.assertEqual(self.out('reg_we'), 1)
        self.assertEqual(self.out('alu_op'), ALU_SUB)

    def test_li(self):
        self.set(opcode=OP_LI)
        self.tick()
        self.assertEqual(self.out('alu_op'), ALU_PASS)
        self.assertEqual(self.out('use_imm'), 1)

    def test_jump(self):
        self.set(opcode=OP_JMP)
        self.tick()
        self.assertEqual(self.out('is_jump'), 1)

    def test_brcc(self):
        self.set(opcode=OP_BRCC)
        self.tick()
        self.assertEqual(self.out('is_brcc'), 1)

    def test_halt(self):
        self.set(opcode=OP_HLT)
        self.tick()
        self.assertEqual(self.out('is_halt'), 1)

    def test_call(self):
        self.set(opcode=OP_CALL)
        self.tick()
        self.assertEqual(self.out('is_call'), 1)

    def test_cmp(self):
        self.set(opcode=OP_CMP)
        self.tick()
        self.assertEqual(self.out('is_cmp'), 1)
        self.assertEqual(self.out('alu_op'), ALU_SUB)
        self.assertEqual(self.out('reg_we'), 0)

    def test_lui(self):
        self.set(opcode=OP_LUI)
        self.tick()
        self.assertEqual(self.out('alu_op'), ALU_LUI)

    def test_iret(self):
        self.set(opcode=OP_IRET)
        self.tick()
        self.assertEqual(self.out('is_iret'), 1)


class TestDecode(VeripyTestCase):
    def create_module(self):
        return Decode()

    def test_field_extraction(self):
        # ADD r3, r5, r2 → opcode=5, rd=3, rs1=5, rs2=2
        self.set(instr=instr(OP_ADD, rd=3, rs1=5, rs2=2))
        self.tick()
        self.assertEqual(self.out('rd_addr'), 3)
        self.assertEqual(self.out('rs1_addr'), 5)
        self.assertEqual(self.out('rs2_addr'), 2)
        self.assertEqual(self.out('reg_we'), 1)

    def test_rs1_remap_addi(self):
        # ADDI r3, imm → rs1 should read from rd field (3)
        self.set(instr=instr(OP_ADDI, rd=3, rs1=7, imm=5))
        self.tick()
        self.assertEqual(self.out('rs1_addr'), 3)  # remapped from rd

    def test_rs1_remap_jmp(self):
        self.set(instr=instr(OP_JMP, rd=5))
        self.tick()
        self.assertEqual(self.out('rs1_addr'), 5)  # remapped from rd

    def test_rs2_remap_store(self):
        # STORE reads data from rd via rs2
        self.set(instr=instr(OP_STORE, rd=4, rs1=2, imm=10))
        self.tick()
        self.assertEqual(self.out('rs2_addr'), 4)  # remapped from rd

    def test_immediate_zero_extend(self):
        self.set(instr=instr(OP_LI, rd=1, imm=0xAB))
        self.tick()
        self.assertEqual(self.out('immediate'), 0x00AB)

    def test_branch_offset_positive(self):
        self.set(instr=instr(OP_BRCC, rd=0, imm=0x10))
        self.tick()
        self.assertEqual(self.out('branch_offset'), 0x0010)

    def test_branch_offset_negative(self):
        self.set(instr=instr(OP_BRCC, rd=0, imm=0xFC))  # -4
        self.tick()
        self.assertEqual(self.out('branch_offset'), 0xFFFC)

    def test_branch_cond(self):
        # BRcc with cond=5 in instr[10:8]
        self.set(instr=instr(OP_BRCC, rd=5, imm=2))
        self.tick()
        self.assertEqual(self.out('branch_cond'), 5)

    def test_control_passthrough(self):
        self.set(instr=instr(OP_LOAD, rd=1, rs1=2, imm=4))
        self.tick()
        self.assertEqual(self.out('reg_we'), 1)
        self.assertEqual(self.out('mem_re'), 1)
        self.assertEqual(self.out('mem_to_reg'), 1)
        self.assertEqual(self.out('use_imm'), 1)
