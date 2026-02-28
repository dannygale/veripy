#!/usr/bin/env python3
"""Benchmark: Python SimEngine vs iverilog simulation throughput."""

import sys, os, time, subprocess, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register
from veripy.sim import SimEngine
from veripy.emit_verilog import _to_snake
from examples.spi_controller import SpiController, sync_fifo


# ── Benchmark modules ────────────────────────────────────────────────

class Counter(Module):
    """Trivial: single register."""
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


# ── Python sim benchmark ─────────────────────────────────────────────

def bench_python(make_module, n_cycles, setup=None):
    """Run SimEngine for n_cycles, return elapsed seconds."""
    mod = make_module()
    sim = SimEngine(mod)
    sim.clock(mod.clock, 1)

    @sim.initial
    def stim():
        if setup:
            setup(mod)
        for _ in range(n_cycles):
            yield 2  # one full clock period

    start = time.perf_counter()
    sim.run()
    return time.perf_counter() - start


# ── iverilog benchmark ───────────────────────────────────────────────

def bench_iverilog(make_module, n_cycles):
    """Compile + run iverilog for n_cycles, return (compile_sec, run_sec)."""
    mod = make_module()

    # Collect all module definitions
    parts = []
    seen = set()
    def _collect(m, mname):
        if mname in seen:
            return
        seen.add(mname)
        factory = getattr(type(m), '_veripy_factory', None)
        fresh = factory() if factory else type(m)()
        for sn, sub in fresh._submodules().items():
            _collect(sub, _to_snake(type(sub).__name__))
        parts.append(fresh.to_verilog(mname))
    for sn, sub in mod._submodules().items():
        _collect(sub, _to_snake(type(sub).__name__))
    parts.append(mod.to_verilog())
    verilog_src = '\n\n'.join(parts)

    module_name = type(mod).__name__.lower()

    # Testbench: just toggle clock for N cycles
    tb = f"""`timescale 1ns/1ps
module tb;
    reg clock;
    {module_name} dut(.clock(clock));
    initial begin
        clock = 0;
        repeat ({n_cycles * 2}) #1 clock = ~clock;
        $finish;
    end
endmodule
"""

    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, 'bench.v')
        out = os.path.join(d, 'bench.out')
        with open(src, 'w') as f:
            f.write(verilog_src + '\n\n' + tb)

        # Compile
        t0 = time.perf_counter()
        r = subprocess.run(['iverilog', '-o', out, src],
                           capture_output=True, text=True)
        compile_time = time.perf_counter() - t0
        if r.returncode != 0:
            print(f'  iverilog compile error:\n{r.stderr}')
            return None, None

        # Run
        t0 = time.perf_counter()
        subprocess.run(['vvp', out], capture_output=True, text=True)
        run_time = time.perf_counter() - t0

    return compile_time, run_time


# ── Main ─────────────────────────────────────────────────────────────

def fmt_rate(cycles, secs):
    if secs < 0.001:
        return f'{cycles/secs:.0f} cyc/s'
    rate = cycles / secs
    if rate > 1e6:
        return f'{rate/1e6:.1f}M cyc/s'
    if rate > 1e3:
        return f'{rate/1e3:.1f}K cyc/s'
    return f'{rate:.0f} cyc/s'


def p(s=''):
    print(s, flush=True)


def run_bench(name, make_module, cycle_counts, setup=None):
    p(f'\n{"=" * 60}')
    p(f'  {name}')
    p(f'{"=" * 60}')
    p(f'{"Cycles":>10}  {"Python":>10}  {"iverilog":>12}  {"Ratio":>8}')
    p(f'{"-"*10}  {"-"*10}  {"-"*12}  {"-"*8}')

    for n in cycle_counts:
        py = bench_python(make_module, n, setup)
        iv_compile, iv_run = bench_iverilog(make_module, n)
        if iv_run is None:
            p(f'{n:>10}  {py:>10.4f}s  {"ERROR":>12}  {"N/A":>8}')
            continue
        iv_total = iv_compile + iv_run
        ratio = py / iv_total if iv_total > 0 else float('inf')
        p(f'{n:>10}  {py:>10.4f}s  {iv_total:>10.4f}s  {ratio:>7.1f}x')
        p(f'{"":>10}  {fmt_rate(n, py):>10}  '
              f'compile {iv_compile:.3f}s + run {iv_run:.3f}s')


def spi_setup(mod):
    mod.reset.set(1)
    mod.spi.miso.set(0)
    mod.tx_valid.set(0)


if __name__ == '__main__':
    cycles = [1_000, 10_000, 100_000]

    run_bench('Counter (trivial — 1 register)',
              Counter, cycles)

    run_bench('SPI Controller (FSM + FIFO + shift register)',
              lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
              cycles, setup=spi_setup)
