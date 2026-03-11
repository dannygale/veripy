#!/usr/bin/env python3
"""Port of lib/hazard_unit.v — forwarding, stall, flush logic."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output


class HazardUnit(Module):
    def __init__(self, reg_addr_w=3):
        self.id_rs1_addr    = Input(reg_addr_w)
        self.id_rs2_addr    = Input(reg_addr_w)
        self.id_branch_reg  = Input()
        self.id_branch_flag = Input()
        self.id_ex_reg_we   = Input()
        self.id_ex_mem_re   = Input()
        self.id_ex_cmp      = Input()
        self.id_ex_rd_addr  = Input(reg_addr_w)
        self.id_ex_valid    = Input()
        self.id_ex_rs1_addr = Input(reg_addr_w)
        self.id_ex_rs2_addr = Input(reg_addr_w)
        self.ex_mem_reg_we  = Input()
        self.ex_mem_mem_re  = Input()
        self.ex_mem_rd_addr = Input(reg_addr_w)
        self.ex_mem_valid   = Input()
        self.mem_wb_reg_we  = Input()
        self.mem_wb_rd_addr = Input(reg_addr_w)
        self.mem_wb_valid   = Input()
        self.branch_taken   = Input()
        self.fwd_a       = Output(2)
        self.fwd_b       = Output(2)
        self.id_fwd_rs1  = Output(2)
        self.stall       = Output()
        self.flush_if_id = Output()
        self.flush_id_ex = Output()
        super().__init__()

    def rtl(self):
        @self.comb
        def ex_fwd_a():
            if self.ex_mem_valid and self.ex_mem_reg_we and self.ex_mem_rd_addr == self.id_ex_rs1_addr:
                self.fwd_a = 1
            elif self.mem_wb_valid and self.mem_wb_reg_we and self.mem_wb_rd_addr == self.id_ex_rs1_addr:
                self.fwd_a = 2
            else:
                self.fwd_a = 0

        @self.comb
        def ex_fwd_b():
            if self.ex_mem_valid and self.ex_mem_reg_we and self.ex_mem_rd_addr == self.id_ex_rs2_addr:
                self.fwd_b = 1
            elif self.mem_wb_valid and self.mem_wb_reg_we and self.mem_wb_rd_addr == self.id_ex_rs2_addr:
                self.fwd_b = 2
            else:
                self.fwd_b = 0

        @self.comb
        def id_fwd():
            if self.ex_mem_valid and self.ex_mem_reg_we and not self.ex_mem_mem_re and self.ex_mem_rd_addr == self.id_rs1_addr:
                self.id_fwd_rs1 = 1
            elif self.mem_wb_valid and self.mem_wb_reg_we and self.mem_wb_rd_addr == self.id_rs1_addr:
                self.id_fwd_rs1 = 2
            else:
                self.id_fwd_rs1 = 0

        @self.comb
        def stall_logic():
            if self.id_ex_valid and self.id_ex_mem_re and (self.id_ex_rd_addr == self.id_rs1_addr or self.id_ex_rd_addr == self.id_rs2_addr):
                self.stall = 1
            elif self.id_branch_reg and ((self.id_ex_valid and self.id_ex_reg_we and self.id_ex_rd_addr == self.id_rs1_addr) or (self.ex_mem_valid and self.ex_mem_mem_re and self.ex_mem_rd_addr == self.id_rs1_addr)):
                self.stall = 1
            elif self.id_branch_flag and self.id_ex_valid and self.id_ex_cmp:
                self.stall = 1
            else:
                self.stall = 0

        @self.comb
        def flush():
            self.flush_if_id = self.branch_taken
            self.flush_id_ex = self.stall


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench


class HazardUnitTestBench(TestBench):
    def create_module(self):
        return HazardUnit()

    def _defaults(self):
        self.set(
            id_rs1_addr=0, id_rs2_addr=0, id_branch_reg=0, id_branch_flag=0,
            id_ex_reg_we=0, id_ex_mem_re=0, id_ex_cmp=0, id_ex_rd_addr=0,
            id_ex_valid=0, id_ex_rs1_addr=0, id_ex_rs2_addr=0,
            ex_mem_reg_we=0, ex_mem_mem_re=0, ex_mem_rd_addr=0, ex_mem_valid=0,
            mem_wb_reg_we=0, mem_wb_rd_addr=0, mem_wb_valid=0, branch_taken=0,
        )

    def test_no_hazard(self):
        dut = self.dut
        @self.initial
        def stim():
            self._defaults()
            yield 1
            assert dut.stall == 0
            assert dut.fwd_a == 0
            assert dut.fwd_b == 0
        self.run_sim()

    def test_ex_mem_forward_a(self):
        dut = self.dut
        @self.initial
        def stim():
            self._defaults()
            dut.ex_mem_valid = 1; dut.ex_mem_reg_we = 1
            dut.ex_mem_rd_addr = 3; dut.id_ex_rs1_addr = 3
            yield 1
            assert dut.fwd_a == 1
        self.run_sim()

    def test_mem_wb_forward_b(self):
        dut = self.dut
        @self.initial
        def stim():
            self._defaults()
            dut.mem_wb_valid = 1; dut.mem_wb_reg_we = 1
            dut.mem_wb_rd_addr = 5; dut.id_ex_rs2_addr = 5
            yield 1
            assert dut.fwd_b == 2
        self.run_sim()

    def test_load_use_stall(self):
        dut = self.dut
        @self.initial
        def stim():
            self._defaults()
            dut.id_ex_valid = 1; dut.id_ex_mem_re = 1
            dut.id_ex_rd_addr = 2; dut.id_rs1_addr = 2
            yield 1
            assert dut.stall == 1
        self.run_sim()

    def test_branch_taken_flush(self):
        dut = self.dut
        @self.initial
        def stim():
            self._defaults()
            dut.branch_taken = 1
            yield 1
            assert dut.flush_if_id == 1
        self.run_sim()


if __name__ == '__main__':
    h = HazardUnit()
    print(h.to_verilog(module_name='hazard_unit'))
