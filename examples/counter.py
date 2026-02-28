#!/usr/bin/env python3
"""Counter example: simulates in Python AND generates Verilog from the same source."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register


class Counter(Module):
    def __init__(self, n=8):
        self.clock = Input()
        self.reset = Input()
        self.enable = Input()
        self.count = Output(n)
        self.counter = Register(n)
        super().__init__()

        @self.comb
        def drive_output():
            self.count = self.counter

        @self.posedge(self.clock)
        def increment():
            if self.reset:
                self.counter = 0
            elif self.enable:
                self.counter = self.counter + 1


if __name__ == '__main__':
    from veripy.sim import SimEngine

    c = Counter(n=4)
    sim = SimEngine(c)
    sim.clock(c.clock, 10)

    @sim.initial
    def demo():
        c.enable.set(1)
        print("=== Simulation ===")
        for i in range(20):
            c.reset.set(1 if i < 2 else 0)
            yield 10
            print(f"  cycle {i:2d}: reset={int(c.reset)} en={int(c.enable)} "
                  f"counter={int(c.counter):2d} count={int(c.count):2d}")

    sim.run()

    print("\n=== Generated Verilog ===")
    print(c.to_verilog())
