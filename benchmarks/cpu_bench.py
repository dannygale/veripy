#!/usr/bin/env python3
"""CPU benchmark: csim vs vvp running programs on cpu_v1.

Assembles programs using cpu_v1's instruction encoders, loads them into
the CPU's memory, and benchmarks simulation performance across backends.
"""

import sys, os, time, tempfile, subprocess, shutil, ctypes

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from examples.cpu_v1 import (
    CPU, prog_fibonacci, prog_sum_array, prog_bubble_sort,
    MEM_DEPTH, DATA_W,
)


# ── Build infrastructure ────────────────────────────────────────────

def _build_cpu_ir():
    """Lower and flatten the CPU IR."""
    from veripy.lower import lower_module
    from veripy.flatten import flatten_ir, topo_sort_comb
    from veripy.backend_csim import _inline_cont_assigns

    cpu = CPU()
    ir = lower_module(cpu, 'cpu')
    flat = flatten_ir(ir, {})
    flat = topo_sort_comb(flat)
    flat = _inline_cont_assigns(flat)
    return cpu, flat


def _gen_c_bench(model_c, program, max_cycles=500000):
    """Generate combined C source: model + testbench that loads program."""
    prog_init = '\n'.join(
        f'    s->imem[{i}] = 0x{w:04X}U;'
        for i, w in enumerate(program))
    nop_fill = f"""
    for (int _i = {len(program)}; _i < {MEM_DEPTH}; _i++)
        s->imem[_i] = 0x0000U;"""

    bench = f"""
/* ── Testbench ─────────────────────────────────── */

uint64_t run_bench(void) {{
    void* p = veripy_create();
    State* s = (State*)p;

    veripy_set_clock(p, 0);
    veripy_set_reset(p, 1);
    veripy_eval(p);

{prog_init}
{nop_fill}

    /* Reset pulse */
    veripy_set_clock(p, 1); veripy_eval(p);
    veripy_set_clock(p, 0); veripy_eval(p);
    veripy_set_clock(p, 1); veripy_eval(p);
    veripy_set_reset(p, 0);

    /* Run until halted */
    uint64_t cycles = 0;
    for (cycles = 0; cycles < {max_cycles}ULL; cycles++) {{
        veripy_set_clock(p, 0); veripy_eval(p);
        veripy_set_clock(p, 1); veripy_eval(p);
        if (veripy_get_halted(p)) break;
    }}

    veripy_destroy(p);
    return cycles;
}}
"""
    return model_c + bench


def compile_csim(ir, program, max_cycles=500000):
    """Compile csim benchmark, return (run_fn, compile_time, cleanup_fn)."""
    from veripy.backend_csim import emit_c

    t0 = time.perf_counter()
    model_c = emit_c(ir)
    combined = _gen_c_bench(model_c, program, max_cycles)

    build_dir = tempfile.mkdtemp(prefix='veripy_cpu_csim_')
    c_path = os.path.join(build_dir, 'bench.c')
    with open(c_path, 'w') as f:
        f.write(combined)

    ext = '.dylib' if os.uname().sysname == 'Darwin' else '.so'
    lib_path = os.path.join(build_dir, f'libbench{ext}')
    flag = '-dynamiclib' if ext == '.dylib' else '-shared'
    cc = os.environ.get('CC', 'cc')

    r = subprocess.run(
        [cc, '-O3', '-march=native', '-flto', '-fPIC', flag,
         '-o', lib_path, c_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'csim compile failed:\n{r.stderr}')

    ct = time.perf_counter() - t0
    lib = ctypes.CDLL(lib_path)
    lib.run_bench.restype = ctypes.c_uint64

    def run():
        return lib.run_bench()

    def cleanup():
        shutil.rmtree(build_dir, ignore_errors=True)

    return run, ct, cleanup


def compile_vvp(program, max_cycles=500000):
    """Compile iverilog benchmark using cpu_v1 Verilog, return (run_fn, ct, cleanup)."""
    t0 = time.perf_counter()

    # Generate Verilog from cpu_v1
    cpu = CPU()
    verilog = cpu.to_verilog(module_name='cpu')

    tb = f"""`timescale 1ns/1ps
module tb;
    reg clock, reset;
    wire halted;
    cpu uut (.clock(clock), .reset(reset), .halted(halted));
    always #5 clock = ~clock;
    integer cycles;
    initial begin
        clock = 0; reset = 1;
        $readmemh("prog.hex", uut.imem);
        @(posedge clock); @(posedge clock); reset = 0;
        cycles = 0;
        begin : run_block
            repeat ({max_cycles}) begin
                @(posedge clock);
                cycles = cycles + 1;
                if (halted) disable run_block;
            end
        end
        $display("CYCLES=%0d", cycles);
        $finish;
    end
endmodule
"""

    build_dir = tempfile.mkdtemp(prefix='veripy_cpu_vvp_')
    v_path = os.path.join(build_dir, 'cpu.v')
    tb_path = os.path.join(build_dir, 'tb.v')
    hex_path = os.path.join(build_dir, 'prog.hex')
    sim_path = os.path.join(build_dir, 'sim')

    with open(v_path, 'w') as f:
        f.write(verilog)
    with open(tb_path, 'w') as f:
        f.write(tb)
    with open(hex_path, 'w') as f:
        for w in program:
            f.write(f'{w & 0xFFFF:04x}\n')

    r = subprocess.run(['iverilog', '-o', sim_path, v_path, tb_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'iverilog failed:\n{r.stderr}')
    ct = time.perf_counter() - t0

    def run():
        r = subprocess.run(['vvp', sim_path], capture_output=True, text=True,
                           cwd=build_dir)
        for line in r.stdout.split('\n'):
            if line.startswith('CYCLES='):
                return int(line.split('=')[1])
        return max_cycles

    def cleanup():
        shutil.rmtree(build_dir, ignore_errors=True)

    return run, ct, cleanup


# ── Main ────────────────────────────────────────────────────────────

def main():
    print('Building CPU IR...')
    module, ir = _build_cpu_ir()
    print(f'  {len(ir.comb_blocks)} comb, {len(ir.seq_blocks)} seq, '
          f'{len(ir.mems)} mems\n')

    programs = {
        'fibonacci(200)': prog_fibonacci(200),
        'sum_array(200)': prog_sum_array(200),
        'bubble_sort(32)': prog_bubble_sort(32),
    }

    # Quick correctness check
    print('Correctness check (fib(10) = expected 55 cycles)...')
    tiny = prog_fibonacci(10)
    run_c, _, cl_c = compile_csim(ir, tiny, max_cycles=10000)
    cyc_csim = run_c(); cl_c()

    try:
        run_v, _, cl_v = compile_vvp(tiny, max_cycles=10000)
        cyc_vvp = run_v(); cl_v()
        print(f'  csim={cyc_csim} cycles, vvp={cyc_vvp} cycles')
        if cyc_csim >= 10000 or cyc_vvp >= 10000:
            print('  ERROR: one or more backends did not halt!')
            return
        if cyc_csim != cyc_vvp:
            print(f'  WARNING: cycle count mismatch csim={cyc_csim} vvp={cyc_vvp}')
        else:
            print('  PASS: cycle counts match')
    except Exception as e:
        print(f'  vvp unavailable ({e}), csim={cyc_csim} cycles')
        if cyc_csim >= 10000:
            print('  ERROR: csim did not halt!')
            return
    print()

    results = []
    for name, prog in programs.items():
        print(f'--- {name} ({len(prog)} words) ---')

        # csim
        run_c, ct_c, cl_c = compile_csim(ir, prog)
        t0 = time.perf_counter()
        cyc_c = run_c()
        exec_c = time.perf_counter() - t0
        cl_c()
        print(f'  csim: compile={ct_c:.2f}s  exec={exec_c:.4f}s  cycles={cyc_c}')

        # vvp (optional)
        try:
            run_v, ct_v, cl_v = compile_vvp(prog)
            t0 = time.perf_counter()
            cyc_v = run_v()
            exec_v = time.perf_counter() - t0
            cl_v()
            print(f'  vvp:  compile={ct_v:.2f}s  exec={exec_v:.4f}s  cycles={cyc_v}')
            speedup = (ct_v + exec_v) / (ct_c + exec_c)
            print(f'  csim speedup vs vvp: {speedup:.1f}x')
            results.append((name, cyc_c, ct_c, exec_c, ct_v, exec_v))
        except Exception as e:
            print(f'  vvp: unavailable ({e})')
            results.append((name, cyc_c, ct_c, exec_c, None, None))
        print()

    # Summary
    print('=' * 70)
    print(f'{"Program":<20} {"Cycles":>7} {"csim total":>12} {"vvp total":>12} {"speedup":>8}')
    print('-' * 70)
    for name, cyc, ct_c, e_c, ct_v, e_v in results:
        total_c = ct_c + e_c
        if ct_v is not None:
            total_v = ct_v + e_v
            spd = f'{total_v/total_c:.1f}x'
        else:
            total_v = None
            spd = 'N/A'
        vvp_str = f'{total_v:>10.2f}s' if total_v else '         N/A'
        print(f'{name:<20} {cyc:>7} {total_c:>10.2f}s {vvp_str} {spd:>8}')


if __name__ == '__main__':
    main()
