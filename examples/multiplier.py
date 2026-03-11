#!/usr/bin/env python3
"""Pipelined multiplier — three simulation tiers from one source.

  functional — instant: result = a * b, no latency
  cycle      — 2-cycle pipeline latency, no structural detail
  rtl        — 2-stage pipeline registers
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import module, Input, Output, Register, posedge
from veripy.context import comb, always, functional, cycle


@module
def pipelined_multiplier(width=16):
    clock  = Input()
    reset  = Input()
    a      = Input(width)
    b      = Input(width)
    valid  = Input()
    result = Output(width * 2)
    done   = Output()

    # Stage 1 registers: capture inputs
    s1_a     = Register(width)
    s1_b     = Register(width)
    s1_valid = Register()

    # Stage 2 registers: capture product
    s2_result = Register(width * 2)
    s2_valid  = Register()

    w = int(width)
    mask = (1 << (w * 2)) - 1

    # ── Tier 1: functional — instant result ──────────────────────
    @functional
    def model():
        if valid._val:
            result._val = (a._val * b._val) & mask
            done._val = 1
        else:
            done._val = 0

    # ── Tier 2: cycle — 2-cycle latency ──────────────────────────
    @cycle(posedge(clock), init=dict(pipe=[(0, 0), (0, 0)]))
    def cycle_model(pipe):
        r, v = pipe[1]
        result._val = r & mask
        done._val = v
        pipe[1] = pipe[0]
        pipe[0] = ((a._val * b._val) & mask, 1) if valid._val else (0, 0)

    # ── Tier 3: RTL — 2-stage pipeline ───────────────────────────
    @comb
    def output():
        result = s2_result
        done = s2_valid

    @always(posedge(clock))
    def stage1():
        """Register inputs."""
        if reset:
            s1_a = 0
            s1_b = 0
            s1_valid = 0
        else:
            s1_a = a
            s1_b = b
            s1_valid = valid

    @always(posedge(clock))
    def stage2():
        """Compute product from registered inputs."""
        if reset:
            s2_result = 0
            s2_valid = 0
        elif s1_valid:
            s2_result = s1_a * s1_b
            s2_valid = 1
        else:
            s2_valid = 0


# ── TestBench ────────────────────────────────────────────────────────

from veripy.verify import TestBench, initial


class MultiplierTestBench(TestBench):

    def create_module(self):
        return pipelined_multiplier(width=16)

    def test_basic_multiply(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stim():
            dut.reset = 1; dut.valid = 0
            yield 10
            dut.reset = 0
            dut.a = 7; dut.b = 6; dut.valid = 1
            yield 10
            dut.valid = 0
            yield 10
            assert dut.result == 42
            assert dut.done == 1

    def test_back_to_back(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stim():
            dut.reset = 1; dut.valid = 0
            yield 10
            dut.reset = 0

            # Two multiplies on consecutive cycles
            dut.a = 100; dut.b = 200; dut.valid = 1
            yield 10
            dut.a = 0xFF; dut.b = 0xFF
            yield 10
            dut.valid = 0

            # First result emerges (100 × 200)
            assert dut.result == 20000
            assert dut.done == 1

            # Second result emerges (255 × 255)
            yield 10
            assert dut.result == 0xFE01
            assert dut.done == 1

    def test_zero(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stim():
            dut.reset = 1; dut.valid = 0
            yield 10
            dut.reset = 0
            dut.a = 12345; dut.b = 0; dut.valid = 1
            yield 10
            dut.valid = 0
            yield 10
            assert dut.result == 0

    def test_max_values(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stim():
            dut.reset = 1; dut.valid = 0
            yield 10
            dut.reset = 0
            dut.a = 0xFFFF; dut.b = 0xFFFF; dut.valid = 1
            yield 10
            dut.valid = 0
            yield 10
            assert dut.result == 0xFFFF * 0xFFFF


if __name__ == '__main__':
    m = pipelined_multiplier(width=16)
    print(m.to_verilog(module_name='pipelined_multiplier'))
