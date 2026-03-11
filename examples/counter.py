#!/usr/bin/env python3
"""Counter example: simulates in Python AND generates Verilog from the same source."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import module, Input, Output, Register, posedge
from veripy.context import comb, always, functional, cycle


@module
def counter(n=8):
    clock   = Input()
    reset   = Input()
    enable  = Input()
    count   = Output(n)
    cnt     = Register(n)

    mask = (1 << int(n)) - 1

    @functional
    def model():
        if enable._val and not reset._val:
            count._val = (count._val + 1) & mask
        elif reset._val:
            count._val = 0

    @cycle(posedge(clock), init=dict(c=0))
    def cycle_model(c):
        count._val = c
        if reset._val:
            c = 0
        elif enable._val:
            c = (c + 1) & mask

    @comb
    def drive_output():
        count = cnt

    @always(posedge(clock))
    def increment():
        if reset:
            cnt = 0
        elif enable:
            cnt = cnt + 1


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench, initial


class CounterTestBench(TestBench):

    def create_module(self):
        return counter(n=4)

    def test_reset_clears(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stim():
            dut.enable = 1; dut.reset = 1
            yield 10
            assert dut.count == 0

    def test_counts_up(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stim():
            dut.enable = 1; dut.reset = 1
            yield 10
            dut.reset = 0
            for i in range(1, 6):
                yield 10
                assert dut.count == i

    def test_enable_gate(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stim():
            dut.enable = 1; dut.reset = 1
            yield 10
            dut.reset = 0
            yield 10
            dut.enable = 0
            yield 30
            assert dut.count == 1


if __name__ == '__main__':
    m = counter(n=4)
    print(m.to_verilog(module_name='counter'))
