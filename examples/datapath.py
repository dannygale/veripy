#!/usr/bin/env python3
"""Datapath example: ALU sub-module wired into a pipeline register.

Demonstrates module instantiation — the ALU is a child module whose
ports are driven by the parent's comb blocks. The ALU result is
captured into a pipeline register on each posedge.

Three simulation tiers:
  functional — immediate result, no pipeline latency
  cycle      — 1-cycle latency (models the register stage)
  rtl        — structural: ALU + pipeline register
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, OutputReg, Register, posedge


class ALU(Module):
    def __init__(self, width=16):
        self.a      = Input(width)
        self.b      = Input(width)
        self.op     = Input(4)
        self.result = Output(width)
        super().__init__()

    def rtl(self):
        @self.comb
        def compute():
            if self.op == 0:
                self.result = self.a + self.b
            elif self.op == 1:
                self.result = self.a - self.b
            else:
                self.result = 0


class Datapath(Module):
    def __init__(self, width=16):
        self.clock  = Input()
        self.reset  = Input()
        self.a      = Input(width)
        self.b      = Input(width)
        self.op     = Input(4)
        self.result = OutputReg(width)
        self.alu    = ALU(width)
        super().__init__()

        @self.functional
        def model():
            # Tier 1: immediate result, no pipeline
            self.result._val = int(self.alu.result)

    def rtl(self):
        @self.comb
        def wire_alu():
            self.alu.a  = self.a
            self.alu.b  = self.b
            self.alu.op = self.op

        @self.cycle(posedge(self.clock), init=dict(pipe=[0]))
        def cycle_model(pipe):
            # Tier 2: 1-cycle latency
            self.result._val = pipe[0]
            pipe[0] = int(self.alu.result)

        @self.always(posedge(self.clock))
        def pipeline():
            # Tier 3: structural pipeline register
            if self.reset:
                self.result = 0
            else:
                self.result = self.alu.result


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench


class DatapathTestBench(TestBench):

    def create_module(self):
        return Datapath(width=8)

    def test_alu_add(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(reset=1)
            yield 10
            self.set(reset=0, a=10, b=5, op=0)
            yield 10
            self.assertEqual(self.out('result'), 15)

        self.run_sim()

    def test_alu_sub(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(reset=1)
            yield 10
            self.set(reset=0, a=20, b=7, op=1)
            yield 10
            self.assertEqual(self.out('result'), 13)

        self.run_sim()

    def test_reset_clears(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(reset=0, a=10, b=5, op=0)
            yield 10
            self.set(reset=1)
            yield 10
            self.assertEqual(self.out('result'), 0)

        self.run_sim()


if __name__ == '__main__':
    from veripy.sim import SimEngine

    d = Datapath(width=8)
    sim = SimEngine(d)
    sim.clock(d.clock, 10)

    @sim.initial
    def demo():
        d.reset.set(1); yield 10; d.reset.set(0)
        print("=== Simulation ===")
        for i in range(6):
            d.a.set((i + 1) * 10); d.b.set(i + 1); d.op.set(i % 2)
            yield 10
            print(f"  cycle {i}: a={int(d.a):3d} b={int(d.b)} op={'ADD' if int(d.op)==0 else 'SUB'} "
                  f"alu={int(d.alu.result):3d} result={int(d.result):3d}")

    sim.run()

    print("\n=== Generated Verilog (all modules) ===")
    print(d.alu.to_verilog())
    print()
    print(d.to_verilog())
