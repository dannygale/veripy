#!/usr/bin/env python3
"""Benchmark: Python sim vs vvp vs C-sim (ctypes) vs C-sim (native TB) vs Verilator.

Separates compile time from execution time for each backend.
"""

import sys, os, time, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register, VeripyTestCase
from veripy.backend_csim import compile_module as csim_compile, compile_bench
from veripy.lower import lower_tb_block
from veripy.ir import IRModule, InitialBlock, AlwaysBlock, Finish
from examples.spi_controller import SpiController

HAS_VERILATOR = shutil.which('verilator') is not None
if HAS_VERILATOR:
    from veripy.backend_verilator import compile_module as verilator_compile
    from veripy.backend_verilator import compile_verilator_bench
T = 10


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


def p(s=''):
    print(s, flush=True)


# ── Helpers ──────────────────────────────────────────────────────────

def _build_tb_ir(create_module, define_stimulus, n_repeats):
    """Build TB IR from stimulus functions, return (tb_ir, module_instance)."""
    class BenchCase(VeripyTestCase):
        def create_module(self):
            return create_module()
    tc = BenchCase('runTest')
    tc._begin()
    define_stimulus(tc, n_repeats)

    mod = tc._mod
    mod_var = None
    for fn in tc._tb_initial + tc._tb_always:
        if hasattr(fn, '__code__') and fn.__closure__:
            for i, name in enumerate(fn.__code__.co_freevars):
                if fn.__closure__[i].cell_contents is mod:
                    mod_var = name
                    break
        if mod_var:
            break
    if mod_var is None:
        mod_var = 'm'

    tb_ir = IRModule(name='tb')
    for fn in tc._tb_always:
        tb_ir.always_blocks.append(AlwaysBlock(
            lower_tb_block(fn, mod, mod_var, tc._output_names)))
    for fn in tc._tb_initial:
        stmts = lower_tb_block(fn, mod, mod_var, tc._output_names)
        stmts.append(Finish())
        tb_ir.initial_blocks.append(InitialBlock(stmts))
    return tb_ir


# ── Backend runners ──────────────────────────────────────────────────

def bench_python(create_module, define_stimulus, n_repeats):
    class BenchCase(VeripyTestCase):
        def create_module(self):
            return create_module()
    tc = BenchCase('runTest')
    t0 = time.perf_counter()
    tc._begin()
    define_stimulus(tc, n_repeats)
    compile_t = time.perf_counter() - t0
    t0 = time.perf_counter()
    tc.run_sim()
    exec_t = time.perf_counter() - t0
    return compile_t, exec_t


def bench_iverilog(create_module, define_stimulus, n_repeats):
    import subprocess, tempfile
    class BenchCase(VeripyTestCase):
        def create_module(self):
            return create_module()
    tc = BenchCase('runTest')
    tc._begin()
    define_stimulus(tc, n_repeats)
    tc.run_sim()

    _orig_run = subprocess.run
    timings = {}
    phase = ['compile']

    def _timed_run(*a, **kw):
        t0 = time.perf_counter()
        r = _orig_run(*a, **kw)
        elapsed = time.perf_counter() - t0
        if phase[0] == 'compile' and a and a[0] and a[0][0] == 'iverilog':
            timings['compile'] = elapsed
            phase[0] = 'exec'
        elif phase[0] == 'exec' and a and a[0] and a[0][0] == 'vvp':
            timings['exec'] = elapsed
        return r

    subprocess.run = _timed_run
    try:
        tc._run_iverilog()
    finally:
        subprocess.run = _orig_run

    return timings.get('compile', 0), timings.get('exec', 0)


def bench_csim_counter(n_cycles):
    m = Counter()
    t0 = time.perf_counter()
    csim = csim_compile(m, 'counter')
    compile_t = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(n_cycles):
        csim.set('clock', 0); csim.eval()
        csim.set('clock', 1); csim.eval()
    exec_t = time.perf_counter() - t0
    csim.close()
    return compile_t, exec_t


def bench_csim_spi(n_xfers):
    m = SpiController(width=8, fifo_depth=4, clk_div=2)
    t0 = time.perf_counter()
    csim = csim_compile(m, 'spicontroller')
    compile_t = time.perf_counter() - t0
    t0 = time.perf_counter()
    csim.set('reset', 1); csim.set('tx_valid', 0)
    csim.set('tx_data', 0); csim.set('spi_miso', 0)
    csim.step('clock', 2)
    csim.set('reset', 0); csim.step('clock', 2)
    for i in range(n_xfers):
        csim.set('tx_data', i & 0xFF); csim.set('tx_valid', 1)
        csim.step('clock', 1)
        csim.set('tx_valid', 0)
        for _ in range(200):
            csim.step('clock', 1)
            if csim.get('rx_valid'):
                break
    exec_t = time.perf_counter() - t0
    csim.close()
    return compile_t, exec_t


def bench_csim_native(create_module, define_stimulus, n_repeats, module_name=None):
    """Return (compile_time, exec_time) for native C testbench."""
    tb_ir = _build_tb_ir(create_module, define_stimulus, n_repeats)
    module = create_module()
    run_fn, compile_t, cleanup = compile_bench(module, tb_ir, module_name)
    t0 = time.perf_counter()
    run_fn()
    exec_t = time.perf_counter() - t0
    cleanup()
    return compile_t, exec_t


def bench_verilator_counter(n_cycles):
    if not HAS_VERILATOR:
        return None, None
    m = Counter()
    t0 = time.perf_counter()
    vmodel = verilator_compile(m, 'counter')
    compile_t = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(n_cycles):
        vmodel.set('clock', 0); vmodel.eval()
        vmodel.set('clock', 1); vmodel.eval()
    exec_t = time.perf_counter() - t0
    vmodel.close()
    return compile_t, exec_t


def bench_verilator_spi(n_xfers):
    if not HAS_VERILATOR:
        return None, None
    m = SpiController(width=8, fifo_depth=4, clk_div=2)
    t0 = time.perf_counter()
    vmodel = verilator_compile(m, 'spicontroller')
    compile_t = time.perf_counter() - t0
    t0 = time.perf_counter()
    vmodel.set('reset', 1); vmodel.set('tx_valid', 0)
    vmodel.set('tx_data', 0); vmodel.set('spi_miso', 0)
    vmodel.step('clock', 2)
    vmodel.set('reset', 0); vmodel.step('clock', 2)
    for i in range(n_xfers):
        vmodel.set('tx_data', i & 0xFF); vmodel.set('tx_valid', 1)
        vmodel.step('clock', 1)
        vmodel.set('tx_valid', 0)
        for _ in range(200):
            vmodel.step('clock', 1)
            if vmodel.get('rx_valid'):
                break
    exec_t = time.perf_counter() - t0
    vmodel.close()
    return compile_t, exec_t


def bench_verilator_native(create_module, define_stimulus, n_repeats, module_name=None):
    """Return (compile_time, exec_time) for native Verilator testbench."""
    if not HAS_VERILATOR:
        return None, None
    tb_ir = _build_tb_ir(create_module, define_stimulus, n_repeats)
    module = create_module()
    run_fn, compile_t, cleanup = compile_verilator_bench(module, tb_ir, module_name)
    t0 = time.perf_counter()
    run_fn()
    exec_t = time.perf_counter() - t0
    cleanup()
    return compile_t, exec_t


# ── Stimulus definitions ─────────────────────────────────────────────

def counter_stimulus(tc, n_repeats):
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
        m.reset.set(0); yield T * 2
        for i in range(n_repeats):
            m.tx_data.set(i & 0xFF); m.tx_valid.set(1)
            yield T * 2
            m.tx_valid.set(0)
            for _ in range(200):
                yield T * 2
                if int(m.rx_valid):
                    tc.out('rx_data')
                    break


def fmt(v, width=10):
    if v is None:
        return 'N/A'.rjust(width)
    return f'{v:.4f}s'.rjust(width)


def ratio(a, b):
    if a is None or b is None or b == 0:
        return 'N/A'.rjust(8)
    return f'{a/b:.1f}x'.rjust(8)


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    verilator_status = 'available' if HAS_VERILATOR else 'not installed'
    p(f'Verilator: {verilator_status}')

    # ── Compile time comparison ──────────────────────────────────
    p(f'\n{"=" * 82}')
    p(f'  Compile Time')
    p(f'{"=" * 82}')
    p(f'{"Design":>20}  {"iverilog":>10}  {"C-native":>10}  {"Vltr-native":>12}')
    p(f'{"-"*20}  {"-"*10}  {"-"*10}  {"-"*12}')

    ic_counter, _ = bench_iverilog(Counter, counter_stimulus, 1)
    nc_counter, _ = bench_csim_native(Counter, counter_stimulus, 1, 'counter')
    vn_counter, _ = bench_verilator_native(Counter, counter_stimulus, 1, 'counter')
    p(f'{"Counter":>20}  {fmt(ic_counter)}  {fmt(nc_counter)}  {fmt(vn_counter, 12)}')

    ic_spi, _ = bench_iverilog(lambda: SpiController(width=8, fifo_depth=4, clk_div=2), spi_stimulus, 1)
    nc_spi, _ = bench_csim_native(
        lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
        spi_stimulus, 1, 'spicontroller')
    vn_spi, _ = bench_verilator_native(
        lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
        spi_stimulus, 1, 'spicontroller')
    p(f'{"SPI Controller":>20}  {fmt(ic_spi)}  {fmt(nc_spi)}  {fmt(vn_spi, 12)}')

    # ── Counter execution time ───────────────────────────────────
    p(f'\n{"=" * 100}')
    p(f'  Counter — Execution Time Only')
    p(f'{"=" * 100}')
    p(f'{"Cycles":>10}  {"Python":>10}  {"vvp":>10}  {"C-native":>10}  {"Vltr-native":>12}  {"Py/Cnat":>8}  {"vvp/Cnat":>8}  {"Vltr/Cnat":>9}')
    p(f'{"-"*10}  {"-"*10}  {"-"*10}  {"-"*10}  {"-"*12}  {"-"*8}  {"-"*8}  {"-"*9}')

    for n in [100, 1_000, 5_000]:
        _, py = bench_python(Counter, counter_stimulus, n)
        _, iv = bench_iverilog(Counter, counter_stimulus, n)
        _, nt = bench_csim_native(Counter, counter_stimulus, n, 'counter')
        _, vn = bench_verilator_native(Counter, counter_stimulus, n, 'counter')
        p(f'{n:>10}  {fmt(py)}  {fmt(iv)}  {fmt(nt)}  {fmt(vn, 12)}  {ratio(py, nt)}  {ratio(iv, nt)}  {ratio(vn, nt):>9}')

    # ── SPI execution time ───────────────────────────────────────
    p(f'\n{"=" * 110}')
    p(f'  SPI Controller — Execution Time Only')
    p(f'{"=" * 110}')
    p(f'{"Xfers":>10}  {"~Cycles":>10}  {"Python":>10}  {"vvp":>10}  {"C-native":>10}  {"Vltr-native":>12}  {"Py/Cnat":>8}  {"vvp/Cnat":>8}  {"Vltr/Cnat":>9}')
    p(f'{"-"*10}  {"-"*10}  {"-"*10}  {"-"*10}  {"-"*10}  {"-"*12}  {"-"*8}  {"-"*8}  {"-"*9}')

    for n in [1, 10, 50, 100, 1000, 10000]:
        _, py = bench_python(
            lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
            spi_stimulus, n)
        _, iv = bench_iverilog(
            lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
            spi_stimulus, n)
        _, nt = bench_csim_native(
            lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
            spi_stimulus, n, 'spicontroller')
        _, vn = bench_verilator_native(
            lambda: SpiController(width=8, fifo_depth=4, clk_div=2),
            spi_stimulus, n, 'spicontroller')
        cyc = f'~{n*38}'
        p(f'{n:>10}  {cyc:>10}  {fmt(py)}  {fmt(iv)}  {fmt(nt)}  {fmt(vn, 12)}  {ratio(py, nt)}  {ratio(iv, nt)}  {ratio(vn, nt):>9}')
