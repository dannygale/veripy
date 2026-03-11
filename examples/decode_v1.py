#!/usr/bin/env python3
"""Port of isa/v1/decode.v + control_unit.v"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output

# Opcodes (from opcodes.vh)
OP_NOP   = 0x00; OP_LOAD  = 0x01; OP_STORE = 0x02; OP_MOV   = 0x03
OP_LI    = 0x04; OP_ADD   = 0x05; OP_SUB   = 0x06; OP_AND   = 0x07
OP_OR    = 0x08; OP_XOR   = 0x09; OP_NOT   = 0x0A; OP_SHL   = 0x0B
OP_SHR   = 0x0C; OP_JMP   = 0x0D; OP_BRCC  = 0x0E; OP_HLT   = 0x0F
OP_IRET  = 0x10; OP_CALL  = 0x11; OP_RET   = 0x12; OP_ADDI  = 0x13
OP_CMP   = 0x14; OP_SUBI  = 0x15; OP_LUI   = 0x19; OP_SRA   = 0x1A

# ALU codes
ALU_ADD = 0; ALU_SUB = 1; ALU_AND = 2; ALU_OR = 3; ALU_XOR = 4
ALU_NOT = 5; ALU_SHL = 6; ALU_SHR = 7; ALU_SRA = 8; ALU_PASS = 9; ALU_LUI = 10


class ControlUnit(Module):
    def __init__(self):
        self.opcode    = Input(5)
        self.reg_we    = Output()
        self.mem_re    = Output()
        self.mem_we    = Output()
        self.use_imm   = Output()
        self.mem_to_reg = Output()
        self.is_jump   = Output()
        self.is_brcc   = Output()
        self.is_call   = Output()
        self.is_ret    = Output()
        self.is_cmp    = Output()
        self.is_halt   = Output()
        self.is_iret   = Output()
        self.alu_op    = Output(4)
        super().__init__()

    def rtl(self):
        @self.comb
        def decode():
            self.reg_we = 0;    self.mem_re = 0;    self.mem_we = 0
            self.use_imm = 0;   self.mem_to_reg = 0
            self.is_jump = 0;   self.is_brcc = 0;   self.is_call = 0
            self.is_ret = 0;    self.is_cmp = 0;    self.is_halt = 0
            self.is_iret = 0;   self.alu_op = ALU_ADD

            if self.opcode == OP_LOAD:
                self.reg_we = 1; self.mem_re = 1
                self.mem_to_reg = 1; self.use_imm = 1
            elif self.opcode == OP_STORE:
                self.mem_we = 1; self.use_imm = 1
            elif self.opcode == OP_MOV:
                self.reg_we = 1
            elif self.opcode == OP_LI:
                self.reg_we = 1; self.use_imm = 1; self.alu_op = ALU_PASS
            elif self.opcode == OP_ADD:
                self.reg_we = 1
            elif self.opcode == OP_SUB:
                self.reg_we = 1; self.alu_op = ALU_SUB
            elif self.opcode == OP_AND:
                self.reg_we = 1; self.alu_op = ALU_AND
            elif self.opcode == OP_OR:
                self.reg_we = 1; self.alu_op = ALU_OR
            elif self.opcode == OP_XOR:
                self.reg_we = 1; self.alu_op = ALU_XOR
            elif self.opcode == OP_NOT:
                self.reg_we = 1; self.alu_op = ALU_NOT
            elif self.opcode == OP_SHL:
                self.reg_we = 1; self.alu_op = ALU_SHL
            elif self.opcode == OP_SHR:
                self.reg_we = 1; self.alu_op = ALU_SHR
            elif self.opcode == OP_SRA:
                self.reg_we = 1; self.alu_op = ALU_SRA
            elif self.opcode == OP_JMP:
                self.is_jump = 1; self.use_imm = 1
            elif self.opcode == OP_BRCC:
                self.is_brcc = 1; self.use_imm = 1
            elif self.opcode == OP_HLT:
                self.is_halt = 1
            elif self.opcode == OP_CALL:
                self.is_call = 1; self.use_imm = 1
            elif self.opcode == OP_RET:
                self.is_ret = 1
            elif self.opcode == OP_ADDI:
                self.reg_we = 1; self.use_imm = 1
            elif self.opcode == OP_CMP:
                self.is_cmp = 1; self.alu_op = ALU_SUB
            elif self.opcode == OP_SUBI:
                self.reg_we = 1; self.use_imm = 1; self.alu_op = ALU_SUB
            elif self.opcode == OP_LUI:
                self.reg_we = 1; self.use_imm = 1; self.alu_op = ALU_LUI
            elif self.opcode == OP_IRET:
                self.is_iret = 1


class Decode(Module):
    def __init__(self, data_width=16, reg_addr_w=3):
        self.instr         = Input(16)
        self.rd_addr       = Output(reg_addr_w)
        self.rs1_addr      = Output(reg_addr_w)
        self.rs2_addr      = Output(reg_addr_w)
        self.immediate     = Output(data_width)
        self.branch_offset = Output(data_width)
        self.alu_op        = Output(4)
        self.reg_we        = Output()
        self.mem_re        = Output()
        self.mem_we        = Output()
        self.use_imm       = Output()
        self.mem_to_reg    = Output()
        self.is_jump       = Output()
        self.is_brcc       = Output()
        self.is_call       = Output()
        self.is_ret        = Output()
        self.is_cmp        = Output()
        self.is_halt       = Output()
        self.is_iret       = Output()
        self.branch_cond   = Output(3)
        self.ctrl          = ControlUnit()
        super().__init__()

    def rtl(self):
        @self.comb
        def wire_ctrl():
            self.ctrl.opcode = self.instr[15:11]

        @self.comb
        def passthrough():
            self.alu_op     = self.ctrl.alu_op
            self.reg_we     = self.ctrl.reg_we
            self.mem_re     = self.ctrl.mem_re
            self.mem_we     = self.ctrl.mem_we
            self.use_imm    = self.ctrl.use_imm
            self.mem_to_reg = self.ctrl.mem_to_reg
            self.is_jump    = self.ctrl.is_jump
            self.is_brcc    = self.ctrl.is_brcc
            self.is_call    = self.ctrl.is_call
            self.is_ret     = self.ctrl.is_ret
            self.is_cmp     = self.ctrl.is_cmp
            self.is_halt    = self.ctrl.is_halt
            self.is_iret    = self.ctrl.is_iret

        @self.comb
        def fields():
            self.rd_addr     = self.instr[10:8]
            self.branch_cond = self.instr[10:8]
            self.immediate   = self.instr[7:0]
            self.branch_offset = self.instr[7:0] | (0xFF00 if self.instr[7] else 0)

        @self.comb
        def remap():
            opcode = self.instr[15:11]
            rs1_from_rd = (opcode == OP_ADDI or opcode == OP_SUBI or
                           opcode == OP_JMP or opcode == OP_CALL)
            self.rs1_addr = self.instr[10:8] if rs1_from_rd else self.instr[7:5]
            self.rs2_addr = self.instr[10:8] if opcode == OP_STORE else self.instr[4:2]


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench, initial


class DecodeTestBench(TestBench):
    def create_module(self):
        return Decode()

    def _encode(self, opcode, rd=0, rs1=0, rs2=0, imm=0):
        return (opcode << 11) | (rd << 8) | (imm & 0xFF)

    def test_li(self):
        dut = self.dut
        @initial
        def stim():
            dut.instr = self._encode(OP_LI, rd=2, imm=42)
            yield 1
            assert dut.reg_we == 1
            assert dut.use_imm == 1
            assert dut.rd_addr == 2
            assert dut.immediate == 42

    def test_add(self):
        dut = self.dut
        @initial
        def stim():
            dut.instr = self._encode(OP_ADD, rd=1)
            yield 1
            assert dut.reg_we == 1
            assert dut.alu_op == ALU_ADD

    def test_store(self):
        dut = self.dut
        @initial
        def stim():
            dut.instr = self._encode(OP_STORE, rd=3, imm=10)
            yield 1
            assert dut.mem_we == 1
            assert dut.reg_we == 0

    def test_jmp(self):
        dut = self.dut
        @initial
        def stim():
            dut.instr = self._encode(OP_JMP, rd=0, imm=0x20)
            yield 1
            assert dut.is_jump == 1

    def test_halt(self):
        dut = self.dut
        @initial
        def stim():
            dut.instr = self._encode(OP_HLT)
            yield 1
            assert dut.is_halt == 1


if __name__ == '__main__':
    d = Decode()
    print(d.ctrl.to_verilog('control_unit'))
    print()
    print(d.to_verilog('decode'))
