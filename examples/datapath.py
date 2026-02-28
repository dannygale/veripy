#!/usr/bin/env python3
"""Datapath example: ALU sub-module wired into a pipeline register.

Demonstrates module instantiation — the ALU is a child module whose
ports are driven by the parent's comb blocks. The ALU result is
captured into a pipeline register on each posedge.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register


class ALU(Module):
    def __init__(self, width=16):
        self.a      = Input(width)
        self.b      = Input(width)
        self.op     = Input(4)
        self.result = Output(width)
        super().__init__()

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
        self.result = Output(width)
        self.piped  = Register(width)
        self.alu    = ALU(width)
        super().__init__()

        @self.comb
        def wire_alu():
            self.alu.a  = self.a
            self.alu.b  = self.b
            self.alu.op = self.op

        @self.posedge(self.clock)
        def pipeline():
            if self.reset:
                self.piped = 0
            else:
                self.piped = self.alu.result

        @self.comb
        def output():
            self.result = self.piped


if __name__ == '__main__':
    from veripy.emit_verilog import VerilogEmitter
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
                  f"alu={int(d.alu.result):3d} piped={int(d.piped):3d}")

    sim.run()

    print("\n=== Generated Verilog (all modules) ===")
    print(VerilogEmitter(d).emit_all())
