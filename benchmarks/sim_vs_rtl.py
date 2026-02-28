#!/usr/bin/env python3
"""Benchmark: Python SimEngine vs iverilog — same stimulus, both backends.

Uses VeripyTestCase internals so both sides run identical testbenches.
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register, VeripyTestCase
from veripy.sim import SimEngine
from examples.spi_controller import SpiController


class Counter(Module):
    def __init__(self):
        self.clock = Input()
        self.count = Output(8)
        self.cnt   = Register(8)
        super().__init__()
        @self.comb
        def drive():
            self.count = self.cnt
        @self.posedge(self.clock)
        def inc():
            self.cnt = self.cnt + 1

T = 10  # half-period, matches test_spi_controller.py


def p(s=''):
    print(s, flush=True)


def fmt_rate(cycles, secs):
    if secs < 0.001:
        return f'{cycles/secs:.0f} cyc/s'
    rate = cycles / secs
    if rate > 1e6:
        return f'{rate/1e6:.1f}M cyc/s'
    if rate > 1e3:
        return f'{rate/1e3:.1f}K cyc/s'
    return f'{rate:.0f} cyc/s'


def bench_dual(name, create_module, define_stimulus, n_repeats=1):
    """Run the same stimulus on Python sim and iverilog, time each.

    define_stimulus(tc, n_repeats) should register @tc.always / @tc.initial
    blocks on the VeripyTestCase instance.
    """

    class BenchCase(VeripyTestCase):
        def create_module(self):
            return create_module()

    tc = BenchCase('runTest')

    # --- Python sim ---
    tc._begin()
    define_stimulus(tc, n_repeats)
    t0 = time.perf_counter()
    tc.run_sim()
    py_time = time.perf_counter() - t0

    # --- iverilog (compile + run, uses recorded trace from Python run) ---
    t0 = time.perf_counter()
    tc._run_iverilog()
    iv_time = time.perf_counter() - t0

    return py_time, iv_time


# ── Stimulus definitions ─────────────────────────────────────────────

def counter_stimulus(tc, n_repeats):
    m = tc._mod

    @tc.always
    def clock():
        tc.set(clock=0); yield T
        tc.set(clock=1); yield T

    @tc.initial
    def stim():
        for _ in range(n_repeats):
            yield T * 2
            tc.out('count')


def spi_stimulus(tc, n_repeats):
    m = tc._mod

    @tc.always
    def clock():
        tc.set(clock=0); yield T
        tc.set(clock=1); yield T

    @tc.initial
    def stim():
        m.reset.set(1); m.tx_valid.set(0); m.tx_data.set(0); m.spi.miso.set(0)
        yield T * 2
        m.reset.set(0)
        yield T * 2

        for i in range(n_repeats):
            # Push a byte
            m.tx_data.set(i & 0xFF); m.tx_valid.set(1)
            yield T * 2
            m.tx_valid.set(0)

            # Wait for transfer to complete
            for _ in range(200):
                yield T * 2
                if int(m.rx_valid):
                    tc.out('rx_data')
                    break


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    p(f'\n{"=" * 60}')
    p(f'  Counter — same stimulus, both backends')
    p(f'  (iverilog = compile + run of generated testbench)')
    p(f'{"=" * 60}')
    p(f'{"Cycles":>10}  {"Python":>10}  {"iverilog":>10}  {"Ratio":>8}')
    p(f'{"-"*10}  {"-"*10}  {"-"*10}  {"-"*8}')

    for n in [100, 1_000, 5_000]:
        py, iv = bench_dual('counter', Counter, counter_stimulus, n)
        ratio = py / iv if iv > 0 else float('inf')
        p(f'{n:>10}  {py:>10.4f}s  {iv:>10.4f}s  {ratio:>7.1f}x')

    p(f'\n{"=" * 60}')
    p(f'  SPI Controller — real byte transfers, both backends')
    p(f'  (iverilog = compile + run of generated testbench)')
    p(f'{"=" * 60}')
    p(f'{"Xfers":>10}  {"~Cycles":>10}  {"Python":>10}  {"iverilog":>10}  {"Ratio":>8}')
    p(f'{"-"*10}  {"-"*10}  {"-"*10}  {"-"*10}  {"-"*8}')

    for n in [1, 10, 50, 100]:
        py, iv = bench_dual('spi', lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
                            spi_stimulus, n)
        ratio = py / iv if iv > 0 else float('inf')
        p(f'{n:>10}  {"~"+str(n*38):>10}  {py:>10.4f}s  {iv:>10.4f}s  {ratio:>7.1f}x')
