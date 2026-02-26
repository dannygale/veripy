#!/usr/bin/env python3
"""Pipeline register with enable, flush, stall, and forwarding mux.

Stress-tests the veripy syntax with a realistic datapath component:
- Pipeline register: captures data on posedge when enabled, clears on flush
- Forwarding mux: selects between register file, EX forward, MEM forward
- Stall logic: freezes the register when stall is asserted
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register


class PipeReg(Module):
    """Pipeline register with flush/stall control."""

    def __init__(self, width=16):
        self.clock   = Input()
        self.reset   = Input()
        self.flush   = Input()
        self.stall   = Input()
        self.d       = Input(width)
        self.q       = Output(width)
        self.data    = Register(width)
        super().__init__()

        @self.comb
        def drive():
            self.q <<= self.data

        @self.posedge(self.clock)
        def capture():
            if self.reset or self.flush:
                self.data <<= 0
            elif not self.stall:
                self.data <<= self.d


class ForwardMux(Module):
    """3-input forwarding mux: reg_file, EX bypass, MEM bypass."""

    def __init__(self, width=16):
        self.clock       = Input()
        # Source addresses (for match detection)
        self.rs_addr     = Input(3)
        self.ex_rd_addr  = Input(3)
        self.mem_rd_addr = Input(3)
        # Write-enable signals (only forward if destination is being written)
        self.ex_we       = Input()
        self.mem_we      = Input()
        # Data sources
        self.reg_val     = Input(width)
        self.ex_val      = Input(width)
        self.mem_val     = Input(width)
        # Output
        self.out         = Output(width)
        self.fwd_sel     = Output(2)   # 0=reg, 1=EX, 2=MEM (debug)
        super().__init__()

        @self.comb
        def forward():
            # EX has priority over MEM (more recent instruction)
            if self.ex_we and self.rs_addr == self.ex_rd_addr:
                self.out <<= self.ex_val
                self.fwd_sel <<= 1
            elif self.mem_we and self.rs_addr == self.mem_rd_addr:
                self.out <<= self.mem_val
                self.fwd_sel <<= 2
            else:
                self.out <<= self.reg_val
                self.fwd_sel <<= 0


if __name__ == '__main__':
    # --- PipeReg demo ---
    print("=== PipeReg Simulation ===")
    pr = PipeReg(width=8)
    pr.d._val = 0xAB

    for i in range(8):
        pr.reset._val = 1 if i < 1 else 0
        pr.flush._val = 1 if i == 4 else 0
        pr.stall._val = 1 if i == 3 else 0
        pr.d._val = (i * 0x11) & 0xFF
        pr.tick()
        print(f"  cycle {i}: rst={pr.reset._val} flush={pr.flush._val} "
              f"stall={pr.stall._val} d=0x{pr.d._val:02x} q=0x{pr.q._val:02x}")

    print("\n=== PipeReg Verilog ===")
    print(pr.to_verilog(module_name='pipe_reg'))

    # --- ForwardMux demo ---
    print("\n=== ForwardMux Simulation ===")
    fm = ForwardMux(width=8)
    fm.reg_val._val = 0x10
    fm.ex_val._val  = 0xEE
    fm.mem_val._val = 0xDD
    fm.rs_addr._val = 3

    tests = [
        ("no match",       0, 0, 5, 5),   # no forward
        ("EX match",       1, 0, 3, 5),   # EX forward
        ("MEM match",      0, 1, 5, 3),   # MEM forward
        ("both match",     1, 1, 3, 3),   # EX wins (priority)
        ("EX match no we", 0, 0, 3, 5),   # match but no write-enable
    ]
    for desc, ex_we, mem_we, ex_rd, mem_rd in tests:
        fm.ex_we._val = ex_we
        fm.mem_we._val = mem_we
        fm.ex_rd_addr._val = ex_rd
        fm.mem_rd_addr._val = mem_rd
        fm.tick()
        print(f"  {desc:20s}: out=0x{fm.out._val:02x} fwd_sel={fm.fwd_sel._val}")

    print("\n=== ForwardMux Verilog ===")
    print(fm.to_verilog(module_name='forward_mux'))
