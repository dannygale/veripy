#!/usr/bin/env python3
"""Benchmark: SimEngine vs SimEngine.compile (cysim) crossover point.

Sweeps design complexity × cycle count to find where cysim compilation
overhead is amortized by faster execution.

Axes:
  complexity — counter (tiny), pipeline (medium), SpiHub (large)
  cycles     — 100, 1k, 10k, 100k, 1M

Output: table of (design, cycles, sim_ms, cysim_compile_ms, cysim_run_ms, cysim_total_ms, speedup)
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import module, Input, Output, Register, Signal, SimEngine
from veripy.context import comb, always
from veripy.signal import posedge


# ── Designs ──────────────────────────────────────────────────────────

# Tiny: 8-bit counter — 1 reg, 1 op
@module
def counter():
    clock = Input()
    reset = Input()
    count = Output(8)
    cnt   = Register(8)

    @comb
    def drive():
        count = cnt

    @always(posedge(clock))
    def inc():
        if reset:
            cnt = 0
        else:
            cnt = cnt + 1


# Medium: 32-bit ALU with deep comb chain — result feeds back through
# several combinatorial stages before being registered
@module
def alu_pipe():
    clock   = Input()
    op      = Input(4)
    a       = Input(32)
    b       = Input(32)
    result  = Output(32)
    flags   = Output(4)   # zero, neg, carry, overflow
    acc     = Register(32)
    acc2    = Register(32)

    # Intermediate combinatorial signals
    sum_ab  = Signal(33)   # a+b with carry
    diff_ab = Signal(33)   # a-b with borrow
    alu_out = Signal(32)
    zero    = Signal()
    neg     = Signal()
    carry   = Signal()
    ovf     = Signal()

    @comb
    def compute():
        sum_ab  = a + b
        diff_ab = a - b
        if op == 0:
            alu_out = sum_ab[31:0]
        elif op == 1:
            alu_out = diff_ab[31:0]
        elif op == 2:
            alu_out = a & b
        elif op == 3:
            alu_out = a | b
        elif op == 4:
            alu_out = a ^ b
        elif op == 5:
            alu_out = a[31:1]          # logical shift right
        elif op == 6:
            alu_out = a * b
        elif op == 7:
            alu_out = acc + b          # accumulate
        elif op == 8:
            alu_out = acc2 ^ a         # xor with second acc
        else:
            alu_out = acc

        zero   = alu_out == 0
        neg    = alu_out[31:31]
        carry  = sum_ab[32:32]
        ovf    = (a[31:31] == b[31:31]) & (alu_out[31:31] != a[31:31])
        flags  = (zero << 3) | (neg << 2) | (carry << 1) | ovf
        result = alu_out

    @always(posedge(clock))
    def latch():
        acc  = alu_out
        acc2 = acc


# Large: use the SPI controller from examples — sub-modules, FSM, FIFO, Mem
def make_spi():
    from examples.spi_controller import SpiController
    return SpiController(width=8, fifo_depth=16, clk_div=4)


DESIGNS = [
    ('counter',  lambda: counter()),
    ('alu_pipe', lambda: alu_pipe()),
    ('spi_ctrl', make_spi),
]

CYCLE_COUNTS = [100, 1_000, 10_000, 100_000, 1_000_000]


# ── Runner ────────────────────────────────────────────────────────────

def run_sim(create_mod, n_cycles):
    mod = create_mod()
    engine = SimEngine(mod)

    def stimulus():
        for i in range(n_cycles * 2):  # 2 half-periods per cycle
            mod._signals()['clock']._val ^= 1
            mod._settle_comb()
            mod._check_edges()
            mod._apply_nba()
            mod._settle_comb()

    t0 = time.perf_counter()
    # Use engine.clock + run_cycles equivalent via manual toggle
    clk = mod._signals().get('clock')

    def _clk():
        while True:
            clk.set(0); yield 5
            clk.set(1); yield 5

    engine.always(_clk)

    def _stop():
        yield n_cycles * 10  # period=10
        engine.finish()

    engine.initial(_stop)
    engine.run()
    return (time.perf_counter() - t0) * 1000


def run_cysim(create_mod, n_cycles):
    mod = create_mod()

    t_compile0 = time.perf_counter()
    engine = SimEngine.compile(mod)
    t_compile = (time.perf_counter() - t_compile0) * 1000

    engine.clock('clock', period=10)

    def _stop():
        yield n_cycles * 10
        engine.finish()

    engine.initial(_stop)

    t_run0 = time.perf_counter()
    engine.run()
    t_run = (time.perf_counter() - t_run0) * 1000

    return t_compile, t_run


# ── Main ──────────────────────────────────────────────────────────────

def print_stats(create):
    from veripy.viz import module_stats
    mod = create()
    name = type(mod).__name__.lower()
    s = module_stats(mod, name=name)
    print(f"  ports:      {s['inputs']} in, {s['outputs']} out")
    print(f"  registers:  {s['registers']} ({s['reg_bits']} bits)")
    print(f"  wires:      {s['wires']} ({s['wire_bits']} bits)")
    if s['memories']:
        print(f"  memories:   {s['memories']} ({s['mem_bits']} bits)")
    if s['instances']:
        print(f"  instances:  {s['instances']}")
    print(f"  blocks:     {s['comb_blocks']} comb, {s['seq_blocks']} seq")
    print(f"  comb depth: {s['comb_depth']}")
    print(f"  logic ops:  {s['logic_ops']}")


def main():
    header = f"{'design':<12} {'cycles':>8}  {'sim_ms':>9}  {'cy_compile':>10}  {'cy_run':>8}  {'cy_total':>9}  {'speedup':>8}"
    print(header)
    print('-' * len(header))

    for name, create in DESIGNS:
        print(f"\n{name}:")
        print_stats(create)
        # Warm up cysim cache with a tiny run so compile time is measured cold once
        _warm = SimEngine.compile(create())

        for n in CYCLE_COUNTS:
            sim_ms = run_sim(create, n)
            cy_compile_ms, cy_run_ms = run_cysim(create, n)
            cy_total_ms = cy_compile_ms + cy_run_ms
            speedup = sim_ms / cy_total_ms if cy_total_ms > 0 else float('inf')

            print(f"{name:<12} {n:>8,}  {sim_ms:>9.1f}  {cy_compile_ms:>10.1f}  {cy_run_ms:>8.1f}  {cy_total_ms:>9.1f}  {speedup:>7.2f}x")
        print()


if __name__ == '__main__':
    main()
