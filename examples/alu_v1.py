#!/usr/bin/env python3
"""Port of isa/v1/alu.v — 11-operation ALU with flags."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Signal

# ALU function codes (match opcodes.vh)
ALU_ADD  = 0
ALU_SUB  = 1
ALU_AND  = 2
ALU_OR   = 3
ALU_XOR  = 4
ALU_NOT  = 5
ALU_SHL  = 6
ALU_SHR  = 7
ALU_SRA  = 8
ALU_PASS = 9
ALU_LUI  = 10


class ALU(Module):
    def __init__(self, width=16):
        self.a      = Input(width)
        self.b      = Input(width)
        self.op     = Input(4)
        self.result = Output(width)
        self.zero   = Output()
        self.carry  = Output()
        self.sign   = Output()
        self._width = width
        super().__init__(params={'WIDTH': width})

    def rtl(self):
        width = self._width

        @self.comb
        def compute():
            self.carry = 0
            if self.op == ALU_ADD:
                self.result = self.a + self.b
                self.carry = 1 if (self.a + self.b) < self.a else 0
            elif self.op == ALU_SUB:
                self.result = self.a - self.b
                self.carry = 1 if self.a < self.b else 0
            elif self.op == ALU_AND:
                self.result = self.a & self.b
            elif self.op == ALU_OR:
                self.result = self.a | self.b
            elif self.op == ALU_XOR:
                self.result = self.a ^ self.b
            elif self.op == ALU_NOT:
                self.result = ~self.a
            elif self.op == ALU_SHL:
                self.result = self.a << 1
                self.carry = self.a[width - 1]
            elif self.op == ALU_SHR:
                self.result = self.a >> 1
            elif self.op == ALU_SRA:
                self.result = (self.a >> 1) | (self.a[width - 1] << (width - 1))
            elif self.op == ALU_PASS:
                self.result = self.b
            elif self.op == ALU_LUI:
                self.result = self.b << 8
            else:
                self.result = 0

        @self.comb
        def flags():
            self.zero = 1 if self.result == 0 else 0
            self.sign = self.result[width - 1]


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench


class ALUTestBench(TestBench):
    def create_module(self):
        return ALU(16)

    def test_add(self):
        dut = self.dut
        @self.initial
        def stim():
            dut.a = 100; dut.b = 50; dut.op = ALU_ADD
            yield 1
            assert dut.result == 150
            assert dut.zero == 0

    def test_sub(self):
        dut = self.dut
        @self.initial
        def stim():
            dut.a = 100; dut.b = 100; dut.op = ALU_SUB
            yield 1
            assert dut.result == 0
            assert dut.zero == 1

    def test_and(self):
        dut = self.dut
        @self.initial
        def stim():
            dut.a = 0xFF0F; dut.b = 0x0FF0; dut.op = ALU_AND
            yield 1
            assert dut.result == 0x0F00

    def test_shl(self):
        dut = self.dut
        @self.initial
        def stim():
            dut.a = 0x8001; dut.b = 0; dut.op = ALU_SHL
            yield 1
            assert dut.result == 0x0002
            assert dut.carry == 1

    def test_pass(self):
        dut = self.dut
        @self.initial
        def stim():
            dut.a = 0; dut.b = 42; dut.op = ALU_PASS
            yield 1
            assert dut.result == 42


if __name__ == '__main__':
    alu = ALU(16)
    print(alu.to_verilog(module_name='alu'))
