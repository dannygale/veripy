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
            self.count <<= self.counter

        @self.posedge(self.clock)
        def increment():
            if self.reset:
                self.counter <<= 0
            elif self.enable:
                self.counter <<= self.counter + 1


if __name__ == '__main__':
    # --- Simulate ---
    c = Counter(n=4)
    c.enable._val = 1

    print("=== Simulation ===")
    for i in range(20):
        c.reset._val = 1 if i < 2 else 0
        c.tick()
        print(f"  cycle {i:2d}: reset={c.reset._val} en={c.enable._val} "
              f"counter={c.counter._val:2d} count={c.count._val:2d}")

    # --- Generate Verilog ---
    print("\n=== Generated Verilog ===")
    print(c.to_verilog())
