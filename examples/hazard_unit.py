#!/usr/bin/env python3
"""Port of lib/hazard_unit.v — forwarding, stall, flush logic."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output


class HazardUnit(Module):
    def __init__(self, reg_addr_w=3):
        # ID stage
        self.id_rs1_addr    = Input(reg_addr_w)
        self.id_rs2_addr    = Input(reg_addr_w)
        self.id_branch_reg  = Input()
        self.id_branch_flag = Input()

        # EX stage (ID/EX pipe reg outputs)
        self.id_ex_reg_we   = Input()
        self.id_ex_mem_re   = Input()
        self.id_ex_cmp      = Input()
        self.id_ex_rd_addr  = Input(reg_addr_w)
        self.id_ex_valid    = Input()
        self.id_ex_rs1_addr = Input(reg_addr_w)
        self.id_ex_rs2_addr = Input(reg_addr_w)

        # EX/MEM forwarding source
        self.ex_mem_reg_we  = Input()
        self.ex_mem_mem_re  = Input()
        self.ex_mem_rd_addr = Input(reg_addr_w)
        self.ex_mem_valid   = Input()

        # MEM/WB forwarding source
        self.mem_wb_reg_we  = Input()
        self.mem_wb_rd_addr = Input(reg_addr_w)
        self.mem_wb_valid   = Input()

        # Branch taken
        self.branch_taken   = Input()

        # Outputs
        self.fwd_a       = Output(2)
        self.fwd_b       = Output(2)
        self.id_fwd_rs1  = Output(2)
        self.stall       = Output()
        self.flush_if_id = Output()
        self.flush_id_ex = Output()
        super().__init__()

        # --- EX-stage forwarding ---
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

        # --- ID-stage forwarding (branch operand) ---
        @self.comb
        def id_fwd():
            if self.ex_mem_valid and self.ex_mem_reg_we and not self.ex_mem_mem_re and self.ex_mem_rd_addr == self.id_rs1_addr:
                self.id_fwd_rs1 = 1
            elif self.mem_wb_valid and self.mem_wb_reg_we and self.mem_wb_rd_addr == self.id_rs1_addr:
                self.id_fwd_rs1 = 2
            else:
                self.id_fwd_rs1 = 0

        # --- Stall ---
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

        # --- Flush ---
        @self.comb
        def flush():
            self.flush_if_id = self.branch_taken
            self.flush_id_ex = self.stall


if __name__ == '__main__':
    h = HazardUnit()
    print(h.to_verilog(module_name='hazard_unit'))
