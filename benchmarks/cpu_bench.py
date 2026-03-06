#!/usr/bin/env python3
"""CPU benchmark: flatten, emit C, compile, compare vs Verilator.

Attempts to compile the RV32I cpu_pipeline from vhdl_cpu/isa/v3.
Falls back to SpiController if the CPU cannot be compiled (e.g. due to
a combinational loop in the flattened design).

Usage:
    python benchmarks/cpu_bench.py [--cpu-path /path/to/vhdl_cpu]
"""

import sys, os, time, shutil, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy.backend_csim import compile_module as csim_compile, compile_bench
from veripy.lower import lower_module, lower_tb_block
from veripy.flatten import flatten_ir, topo_sort_comb
from veripy.backend_csim import _inline_cont_assigns, emit_c
from veripy.ir import IRModule, InitialBlock, AlwaysBlock, Finish
from veripy import VeripyTestCase

HAS_VERILATOR = shutil.which('verilator') is not None
if HAS_VERILATOR:
    from veripy.backend_verilator import compile_module as verilator_compile


def p(s=''):
    print(s, flush=True)


def _try_compile_cpu(cpu_path):
    """Try to compile cpu_pipeline. Returns (module, ir_stats) or None."""
    if not cpu_path or not os.path.isdir(cpu_path):
        return None
    v3 = os.path.join(cpu_path, 'isa', 'v3')
    lib = os.path.join(cpu_path, 'lib', 'v3')
    if not os.path.isdir(v3):
        return None
    sys.path.insert(0, v3)
    sys.path.insert(0, lib)
    try:
        from cpu_pipeline import cpu_pipeline
        m = cpu_pipeline()
        from veripy.emit_verilog import _to_snake
        from veripy.module import Module as _Module
        registry = {}
        def _collect(mod, mname):
            if mname in registry:
                return
            factory = getattr(type(mod), '_veripy_factory', None)
            fresh = factory() if factory else type(mod)()
            for sn, sub in fresh._submodules().items():
                _collect(sub, _to_snake(type(sub).__name__))
            registry[mname] = lower_module(fresh, mname)
        for k in dir(m):
            v = getattr(m, k)
            if isinstance(v, _Module) and v is not m:
                _collect(v, _to_snake(type(v).__name__))
        top_ir = lower_module(m, 'cpu_pipeline')
        flat_ir = flatten_ir(top_ir, registry) if top_ir.instances else top_ir
        flat_ir = topo_sort_comb(flat_ir)
        flat_ir = _inline_cont_assigns(flat_ir)
        stats = (len(flat_ir.ports), len(flat_ir.comb_blocks), len(flat_ir.seq_blocks))
        return m, flat_ir, stats
    except Exception as e:
        return None, None, str(e)


def _bench_csim(module, n_cycles, module_name):
    """Compile and run a simple clock-toggle benchmark."""
    t0 = time.perf_counter()
    model = csim_compile(module, module_name)
    compile_t = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(n_cycles):
        model.set('clock', 0)
        model.eval()
        model.set('clock', 1)
        model.eval()
    exec_t = time.perf_counter() - t0
    model.close()
    return compile_t, exec_t


def _bench_verilator(module, n_cycles, module_name):
    """Compile and run Verilator benchmark."""
    if not HAS_VERILATOR:
        return None, None
    t0 = time.perf_counter()
    with verilator_compile(module, module_name) as vm:
        compile_t = time.perf_counter() - t0
        t0 = time.perf_counter()
        for _ in range(n_cycles):
            vm.set('clock', 0)
            vm.eval()
            vm.set('clock', 1)
            vm.eval()
        exec_t = time.perf_counter() - t0
    return compile_t, exec_t


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cpu-path', default=os.path.join(
        os.path.dirname(__file__), '..', '..', 'vhdl_cpu'))
    args = parser.parse_args()

    p('=== CPU / Large-Design C-sim Benchmark ===')
    p()

    # Try CPU first
    result = _try_compile_cpu(args.cpu_path)
    if result[0] is not None:
        module, flat_ir, (n_ports, n_comb, n_seq) = result
        module_name = 'cpu_pipeline'
        p(f'Design: cpu_pipeline ({n_ports} ports, {n_comb} comb, {n_seq} seq)')
    else:
        _, _, err = result
        p(f'cpu_pipeline unavailable: {err}')
        p('Falling back to SpiController')
        p()
        from examples.spi_controller import SpiController
        module = SpiController()
        module_name = 'spi_controller'
        p('Design: SpiController (fallback)')

    p()
    for n_cycles in [10_000, 100_000, 1_000_000]:
        p(f'--- {n_cycles:,} clock cycles ---')
        ct, et = _bench_csim(module, n_cycles, module_name)
        p(f'  C-native:    compile {ct:.3f}s  exec {et:.4f}s  total {ct+et:.3f}s')
        if HAS_VERILATOR:
            vct, vet = _bench_verilator(module, n_cycles, module_name)
            if vct is not None:
                ratio = vet / et if et > 0 else float('inf')
                p(f'  Vltr-native: compile {vct:.3f}s  exec {vet:.4f}s  total {vct+vet:.3f}s')
                p(f'  Exec ratio (Vltr/C): {ratio:.2f}x')
        p()


if __name__ == '__main__':
    main()
