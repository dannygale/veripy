#!/usr/bin/env python3
"""Pipeline register with enable, flush, stall, and forwarding mux.

Stress-tests the veripy syntax with a realistic datapath component:
- Pipeline register: captures data on posedge when enabled, clears on flush
- Forwarding mux: selects between register file, EX forward, MEM forward
- Stall logic: freezes the register when stall is asserted
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, OutputReg, posedge


class PipeReg(Module):
    """Pipeline register with flush/stall control."""

    def __init__(self, width=16):
        self.clock   = Input()
        self.reset   = Input()
        self.flush   = Input()
        self.stall   = Input()
        self.d       = Input(width)
        self.q       = OutputReg(width)
        super().__init__()

    def rtl(self):
        @self.cycle(posedge(self.clock), init=dict(reg=[0]))
        def cycle_model(reg):
            # Tier 2: cycle-accurate — models flush/stall behavior
            self.q._val = reg[0]
            if self.reset or self.flush:
                reg[0] = 0
            elif not self.stall:
                reg[0] = int(self.d)

        @self.always(posedge(self.clock))
        def capture():
            # Tier 3: RTL
            if self.reset or self.flush:
                self.q = 0
            elif not self.stall:
                self.q = self.d


class ForwardMux(Module):
    """3-input forwarding mux: reg_file, EX bypass, MEM bypass."""

    def __init__(self, width=16):
        self.clock       = Input()
        self.rs_addr     = Input(3)
        self.ex_rd_addr  = Input(3)
        self.mem_rd_addr = Input(3)
        self.ex_we       = Input()
        self.mem_we      = Input()
        self.reg_val     = Input(width)
        self.ex_val      = Input(width)
        self.mem_val     = Input(width)
        self.out         = Output(width)
        self.fwd_sel     = Output(2)
        super().__init__()

    def rtl(self):
        @self.functional
        def model():
            # Tier 1: combinational — immediate forwarding decision
            if self.ex_we and self.rs_addr == self.ex_rd_addr:
                self.out._val = int(self.ex_val)
                self.fwd_sel._val = 1
            elif self.mem_we and self.rs_addr == self.mem_rd_addr:
                self.out._val = int(self.mem_val)
                self.fwd_sel._val = 2
            else:
                self.out._val = int(self.reg_val)
                self.fwd_sel._val = 0

        @self.comb
        def forward():
            if self.ex_we and self.rs_addr == self.ex_rd_addr:
                self.out = self.ex_val
                self.fwd_sel = 1
            elif self.mem_we and self.rs_addr == self.mem_rd_addr:
                self.out = self.mem_val
                self.fwd_sel = 2
            else:
                self.out = self.reg_val
                self.fwd_sel = 0


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench


class PipeRegTestBench(TestBench):

    def create_module(self):
        return PipeReg(width=8)

    def test_captures_data(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(reset=1, stall=0, flush=0)
            yield 10
            self.set(reset=0, d=0xAB)
            yield 10
            self.assertEqual(self.out('q'), 0xAB)

        self.run_sim()

    def test_stall_holds(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(reset=1, stall=0, flush=0)
            yield 10
            self.set(reset=0, d=0x42)
            yield 10
            self.set(stall=1, d=0xFF)
            yield 10
            self.assertEqual(self.out('q'), 0x42)

        self.run_sim()

    def test_flush_clears(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(reset=1, stall=0, flush=0)
            yield 10
            self.set(reset=0, d=0x42)
            yield 10
            self.set(flush=1)
            yield 10
            self.assertEqual(self.out('q'), 0)

        self.run_sim()


class ForwardMuxTestBench(TestBench):
    def create_module(self):
        return ForwardMux(width=8)

    def test_no_forward(self):
        @self.initial
        def stim():
            self.set(rs_addr=3, ex_rd_addr=5, mem_rd_addr=5,
                     ex_we=0, mem_we=0, reg_val=0x10, ex_val=0xEE, mem_val=0xDD)
            yield 1
            self.assertEqual(self.out('out'), 0x10)
            self.assertEqual(self.out('fwd_sel'), 0)
        self.run_sim()

    def test_ex_forward(self):
        @self.initial
        def stim():
            self.set(rs_addr=3, ex_rd_addr=3, mem_rd_addr=5,
                     ex_we=1, mem_we=0, reg_val=0x10, ex_val=0xEE, mem_val=0xDD)
            yield 1
            self.assertEqual(self.out('out'), 0xEE)
            self.assertEqual(self.out('fwd_sel'), 1)
        self.run_sim()

    def test_mem_forward(self):
        @self.initial
        def stim():
            self.set(rs_addr=3, ex_rd_addr=5, mem_rd_addr=3,
                     ex_we=0, mem_we=1, reg_val=0x10, ex_val=0xEE, mem_val=0xDD)
            yield 1
            self.assertEqual(self.out('out'), 0xDD)
            self.assertEqual(self.out('fwd_sel'), 2)
        self.run_sim()

    def test_ex_priority(self):
        @self.initial
        def stim():
            self.set(rs_addr=3, ex_rd_addr=3, mem_rd_addr=3,
                     ex_we=1, mem_we=1, reg_val=0x10, ex_val=0xEE, mem_val=0xDD)
            yield 1
            self.assertEqual(self.out('out'), 0xEE)
            self.assertEqual(self.out('fwd_sel'), 1)
        self.run_sim()


if __name__ == '__main__':
    from veripy.sim import SimEngine

    # --- PipeReg demo ---
    print("=== PipeReg Simulation ===")
    pr = PipeReg(width=8)
    sim = SimEngine(pr)
    sim.clock(pr.clock, 10)

    @sim.initial
    def pipe_demo():
        for i in range(8):
            pr.reset.set(1 if i < 1 else 0)
            pr.flush.set(1 if i == 4 else 0)
            pr.stall.set(1 if i == 3 else 0)
            pr.d.set((i * 0x11) & 0xFF)
            yield 10
            print(f"  cycle {i}: rst={int(pr.reset)} flush={int(pr.flush)} "
                  f"stall={int(pr.stall)} d=0x{int(pr.d):02x} q=0x{int(pr.q):02x}")

    sim.run()

    print("\n=== PipeReg Verilog ===")
    print(pr.to_verilog(module_name='pipe_reg'))

    # --- ForwardMux demo ---
    print("\n=== ForwardMux Simulation ===")
    fm = ForwardMux(width=8)
    fm.reg_val.set(0x10)
    fm.ex_val.set(0xEE)
    fm.mem_val.set(0xDD)
    fm.rs_addr.set(3)

    tests = [
        ("no match",       0, 0, 5, 5),
        ("EX match",       1, 0, 3, 5),
        ("MEM match",      0, 1, 5, 3),
        ("both match",     1, 1, 3, 3),
        ("EX match no we", 0, 0, 3, 5),
    ]
    for desc, ex_we, mem_we, ex_rd, mem_rd in tests:
        fm.ex_we.set(ex_we)
        fm.mem_we.set(mem_we)
        fm.ex_rd_addr.set(ex_rd)
        fm.mem_rd_addr.set(mem_rd)
        fm._settle_comb()
        print(f"  {desc:20s}: out=0x{int(fm.out):02x} fwd_sel={int(fm.fwd_sel)}")

    print("\n=== ForwardMux Verilog ===")
    print(fm.to_verilog(module_name='forward_mux'))
