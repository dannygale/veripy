#!/usr/bin/env python3
"""CPU benchmark: csim vs Verilator running RV32I programs.

Compiles C/assembly programs from benchmarks/programs/ using the vhdl_cpu
toolchain (crt0.S + link.ld), loads them into the CPU's memory, and
benchmarks simulation performance across vvp, csim, and Verilator.
"""

import sys, os, time, tempfile, subprocess, shutil, ctypes, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'vhdl_cpu', 'isa', 'v3'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'vhdl_cpu', 'lib', 'v3'))


# ── Benchmark programs ──────────────────────────────────────────────

_VHDL_CPU = os.path.join(os.path.dirname(__file__), '..', '..', 'vhdl_cpu')
_CRT0 = os.path.join(_VHDL_CPU, 'isa', 'v2', 'programs', 'crt0.S')
_LINK_LD = os.path.join(_VHDL_CPU, 'isa', 'v2', 'programs', 'link.ld')
_PROG_DIR = os.path.join(os.path.dirname(__file__), 'programs')


def compile_program(name):
    """Compile a .c or .S file from benchmarks/programs/ into a word list.

    Uses the vhdl_cpu crt0.S and link.ld so that C programs get a stack,
    zeroed .bss, and a call to main() followed by WFI.
    """
    # Find source file
    for ext in ('.c', '.S'):
        src = os.path.join(_PROG_DIR, name + ext)
        if os.path.exists(src):
            break
    else:
        raise FileNotFoundError(f'No .c or .S found for {name!r} in {_PROG_DIR}')

    cc = 'riscv64-elf-gcc'
    objcopy = 'riscv64-elf-objcopy'
    with tempfile.TemporaryDirectory() as tmp:
        elf_path = os.path.join(tmp, 'prog.elf')
        bin_path = os.path.join(tmp, 'prog.bin')
        srcs = [_CRT0, src] if src.endswith('.c') else [src]
        r = subprocess.run(
            [cc, '-march=rv32i', '-mabi=ilp32', '-O2', '-nostdlib',
             '-T', _LINK_LD, '-o', elf_path] + srcs,
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f'Compile failed for {name}:\n{r.stderr}')
        subprocess.run([objcopy, '-O', 'binary', elf_path, bin_path],
                       capture_output=True, text=True, check=True)
        with open(bin_path, 'rb') as f:
            data = f.read()
    while len(data) % 4:
        data += b'\x00'
    return list(struct.unpack(f'<{len(data)//4}I', data))



# ── Build infrastructure ────────────────────────────────────────────

def _build_cpu_ir():
    """Build flat, optimized IR for the CPU."""
    from cpu import cpu
    from veripy.lower import lower_module
    from veripy.flatten import flatten_ir, topo_sort_comb
    from veripy.backend_csim import _inline_cont_assigns, _collect_submodule_registry

    m = cpu()
    registry, patch_fn = _collect_submodule_registry(m)
    top_ir = lower_module(m, 'cpu')
    patch_fn(top_ir)
    flat = flatten_ir(top_ir, registry)
    flat = topo_sort_comb(flat)
    flat = _inline_cont_assigns(flat)
    return m, flat


def _build_cpu_hier():
    """Build hierarchical IR + registry for the CPU."""
    from cpu import cpu
    from veripy.lower import lower_module
    from veripy.backend_csim import _collect_submodule_registry

    m = cpu()
    registry, patch_fn = _collect_submodule_registry(m)
    top_ir = lower_module(m, 'cpu')
    patch_fn(top_ir)
    return m, top_ir, registry


def _gen_c_bench(model_c, program, max_cycles=500000):
    """Generate combined C source: model + testbench that loads program."""
    prog_init = '\n'.join(
        f'    s->mem_mem[{i}] = 0x{w:08X}U;'
        for i, w in enumerate(program))
    nop_fill = f"""
    for (int _i = {len(program)}; _i < 1024; _i++)
        s->mem_mem[_i] = 0x00000013U;"""

    bench = f"""
/* ── Testbench ─────────────────────────────────── */

uint64_t run_bench(void) {{
    void* p = veripy_create();
    State* s = (State*)p;

    /* Initial eval, then load program (after init zeroes memory) */
    veripy_set_clk(p, 0);
    veripy_set_rst(p, 1);
    veripy_set_ext_mip(p, 0);
    veripy_eval(p);

{prog_init}
{nop_fill}

    /* Reset */
    veripy_set_clk(p, 1); veripy_eval(p);
    veripy_set_clk(p, 0); veripy_eval(p);
    veripy_set_clk(p, 1); veripy_eval(p);
    veripy_set_rst(p, 0);

    /* Run until halted */
    uint64_t cycles = 0;
    for (cycles = 0; cycles < {max_cycles}ULL; cycles++) {{
        veripy_set_clk(p, 0); veripy_eval(p);
        veripy_set_clk(p, 1); veripy_eval(p);
        if (veripy_get_debug_halted(p)) break;
    }}

    veripy_destroy(p);
    return cycles;
}}
"""
    return model_c + bench


def _gen_vltr_bench(program, max_cycles=500000):
    """Generate Verilator C++ testbench."""
    prog_init = '\n'.join(
        f'    mem[{i}] = 0x{w:08X}U;'
        for i, w in enumerate(program))
    nop_fill = f"""
    for (int _i = {len(program)}; _i < 1024; _i++)
        mem[_i] = 0x00000013U;"""

    return f"""#include "Vcpu.h"
#include "verilated.h"
#include "Vcpu___024root.h"

double sc_time_stamp() {{ return 0; }}

extern "C" {{

uint64_t run_bench() {{
    VerilatedContext ctx;
    Vcpu* dut = new Vcpu{{&ctx}};

    /* First eval runs initial blocks (which zero memory) */
    dut->rst = 1;
    dut->ext_mip = 0;
    dut->clk = 0;
    dut->eval();

    /* Load program AFTER initial blocks have run */
    auto& mem = dut->rootp->cpu__DOT__mem__DOT__mem;
{prog_init}
{nop_fill}

    /* Reset */
    dut->clk = 1; dut->eval();
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
    dut->rst = 0;

    /* Run until halted */
    uint64_t cycles = 0;
    for (cycles = 0; cycles < {max_cycles}ULL; cycles++) {{
        dut->clk = 0; dut->eval();
        dut->clk = 1; dut->eval();
        if (dut->debug_halted) break;
    }}

    delete dut;
    return cycles;
}}

}}  // extern "C"
"""


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


def compile_csim_hier(top_ir, registry, program, max_cycles=500000):
    """Compile hierarchical csim benchmark, return (run_fn, compile_time, cleanup_fn)."""
    from veripy.backend_csim import emit_c_hier

    t0 = time.perf_counter()
    model_c = emit_c_hier(top_ir, registry)

    # Hierarchical struct: s->mem.mem[i] instead of s->mem_mem[i]
    prog_init = '\n'.join(
        f'    ((State_cpu*)p)->mem.mem[{i}] = 0x{w:08X}U;'
        for i, w in enumerate(program))
    nop_fill = f"""
    for (int _i = {len(program)}; _i < 1024; _i++)
        ((State_cpu*)p)->mem.mem[_i] = 0x00000013U;"""

    bench = f"""
uint64_t run_bench(void) {{
    void* p = veripy_create();
    veripy_set_clk(p, 0); veripy_set_rst(p, 1); veripy_set_ext_mip(p, 0);
    veripy_eval(p);
{prog_init}
{nop_fill}
    veripy_set_clk(p, 1); veripy_eval(p);
    veripy_set_clk(p, 0); veripy_eval(p);
    veripy_set_clk(p, 1); veripy_eval(p);
    veripy_set_rst(p, 0);
    uint64_t cycles = 0;
    for (cycles = 0; cycles < {max_cycles}ULL; cycles++) {{
        veripy_set_clk(p, 0); veripy_eval(p);
        veripy_set_clk(p, 1); veripy_eval(p);
        if (veripy_get_debug_halted(p)) break;
    }}
    veripy_destroy(p);
    return cycles;
}}
"""
    combined = model_c + bench

    build_dir = tempfile.mkdtemp(prefix='veripy_cpu_hier_')
    c_path = os.path.join(build_dir, 'bench.c')
    with open(c_path, 'w') as f:
        f.write(combined)

    ext = '.dylib' if os.uname().sysname == 'Darwin' else '.so'
    lib_path = os.path.join(build_dir, f'libbench{ext}')
    flag = '-dynamiclib' if ext == '.dylib' else '-shared'
    cc = os.environ.get('CC', 'cc')

    r = subprocess.run(
        [cc, '-O3', '-march=native', '-flto', '-w', '-fPIC', flag, '-o', lib_path, c_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'csim_hier compile failed:\n{r.stderr}')

    ct = time.perf_counter() - t0
    lib = ctypes.CDLL(lib_path)
    lib.run_bench.restype = ctypes.c_uint64

    def run():
        return lib.run_bench()

    def cleanup():
        shutil.rmtree(build_dir, ignore_errors=True)

    return run, ct, cleanup


def compile_verilator(module, program, max_cycles=500000):
    """Compile Verilator benchmark, return (run_fn, compile_time, cleanup_fn)."""
    import glob as _glob

    t0 = time.perf_counter()

    # Use pre-built Verilog files from vhdl_cpu/build/v3/
    vhdl_root = os.path.join(os.path.dirname(__file__), '..', '..', 'vhdl_cpu')
    v_files = [f for f in sorted(_glob.glob(os.path.join(vhdl_root, 'build', 'v3', '*.v')))
               if not os.path.basename(f).startswith('_')]

    build_dir = tempfile.mkdtemp(prefix='veripy_cpu_vltr_')
    obj_dir = os.path.join(build_dir, 'obj_dir')

    # Verilate
    r = subprocess.run(
        ['verilator', '--cc'] + v_files + ['--top', 'cpu', '--Mdir', obj_dir,
         '-CFLAGS', '-fPIC', '-Wno-fatal'],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'verilator failed:\n{r.stderr}')

    r = subprocess.run(['make', '-C', obj_dir, '-f', 'Vcpu.mk', '-j4'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'make failed:\n{r.stderr}')

    # Write testbench
    tb_src = _gen_vltr_bench(program, max_cycles)
    tb_path = os.path.join(build_dir, 'bench.cpp')
    with open(tb_path, 'w') as f:
        f.write(tb_src)

    # Compile shared lib
    vltr_root = subprocess.run(
        ['verilator', '--getenv', 'VERILATOR_ROOT'],
        capture_output=True, text=True).stdout.strip()
    inc_dir = os.path.join(vltr_root, 'include')

    ext = '.dylib' if os.uname().sysname == 'Darwin' else '.so'
    lib_path = os.path.join(build_dir, f'libbench{ext}')
    flag = '-dynamiclib' if ext == '.dylib' else '-shared'

    # Link against the Verilator archive
    archive = os.path.join(obj_dir, 'Vcpu__ALL.a')
    verilated_cpp = os.path.join(inc_dir, 'verilated.cpp')
    verilated_threads = os.path.join(inc_dir, 'verilated_threads.cpp')

    r = subprocess.run(
        ['c++', '-O2', '-fPIC', flag,
         '-I', obj_dir, '-I', inc_dir, '-I', os.path.join(inc_dir, 'vltstd'),
         '-DVERILATOR=1', '-DVM_COVERAGE=0', '-DVM_SC=0', '-DVM_TIMING=0',
         '-DVM_TRACE=0', '-DVM_TRACE_FST=0', '-DVM_TRACE_VCD=0', '-DVM_TRACE_SAIF=0',
         tb_path, verilated_cpp, verilated_threads, archive,
         '-lpthread', '-o', lib_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'Verilator bench compile failed:\n{r.stderr}')

    ct = time.perf_counter() - t0
    lib = ctypes.CDLL(lib_path)
    lib.run_bench.restype = ctypes.c_uint64

    def run():
        return lib.run_bench()

    def cleanup():
        shutil.rmtree(build_dir, ignore_errors=True)

    return run, ct, cleanup


def compile_vvp(program, max_cycles=500000):
    """Compile iverilog benchmark, return (run_fn, compile_time, cleanup_fn)."""
    import glob as _glob

    t0 = time.perf_counter()

    vhdl_root = os.path.join(os.path.dirname(__file__), '..', '..', 'vhdl_cpu')
    v_files = sorted(_glob.glob(os.path.join(vhdl_root, 'build', 'v3', '*.v')))
    inc = [f'-I{os.path.join(vhdl_root, d)}' for d in ['isa/v3', 'lib', 'build/v3']]

    # Generate testbench
    prog_init = '\n'.join(
        f'        uut.mem.mem[{i}] = 32\'h{w & 0xFFFFFFFF:08X};'
        for i, w in enumerate(program))

    tb = f"""`timescale 1ns/1ps
module tb;
    reg clk, rst;
    wire [31:0] debug_pc;
    wire debug_halted;
    cpu #(.MEM_DEPTH(1024), .MEM_LATENCY(3)) uut (
        .clk(clk), .rst(rst), .ext_mip(32'b0),
        .debug_pc(debug_pc), .debug_halted(debug_halted)
    );
    always #5 clk = ~clk;
    integer i, cycles;
    initial begin
        clk = 0; rst = 1;
        for (i = 0; i < 1024; i = i + 1) uut.mem.mem[i] = 32'h00000013;
{prog_init}
        @(posedge clk); @(posedge clk); rst = 0;
        cycles = 0;
        begin : run_block
            repeat ({max_cycles}) begin
                @(posedge clk);
                cycles = cycles + 1;
                if (debug_halted) disable run_block;
            end
        end
        $display("CYCLES=%0d", cycles);
        $finish;
    end
endmodule
"""

    build_dir = tempfile.mkdtemp(prefix='veripy_cpu_vvp_')
    tb_path = os.path.join(build_dir, 'tb.v')
    sim_path = os.path.join(build_dir, 'sim')
    with open(tb_path, 'w') as f:
        f.write(tb)

    r = subprocess.run(['iverilog', '-o', sim_path] + inc + v_files + [tb_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'iverilog failed:\n{r.stderr}')
    ct = time.perf_counter() - t0

    def run():
        r = subprocess.run(['vvp', sim_path], capture_output=True, text=True)
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
    print(f'  flat: {len(ir.comb_blocks)} comb, {len(ir.seq_blocks)} seq, '
          f'{sum(1 for m in ir.mems)} mems')

    print('Building hierarchical IR...')
    _, hier_ir, hier_reg = _build_cpu_hier()
    print(f'  hier: {len(hier_reg)} module types\n')

    programs = {
        'fibonacci':  compile_program('fibonacci'),
        'bubblesort': compile_program('bubblesort'),
        'sieve':      compile_program('sieve'),
    }

    # Quick correctness check — use the fibonacci program
    print('Correctness check (fibonacci)...')
    tiny = programs['fibonacci']
    run_v, _, cl_v = compile_vvp(tiny, max_cycles=50000)
    cyc_vvp = run_v(); cl_v()
    run_c, _, cl_c = compile_csim(ir, tiny, max_cycles=50000)
    cyc_csim = run_c(); cl_c()
    run_h, _, cl_h = compile_csim_hier(hier_ir, hier_reg, tiny, max_cycles=50000)
    cyc_hier = run_h(); cl_h()
    run_vl, _, cl_vl = compile_verilator(module, tiny, max_cycles=50000)
    cyc_vltr = run_vl(); cl_vl()
    print(f'  vvp={cyc_vvp}, csim={cyc_csim}, csim_hier={cyc_hier}, vltr={cyc_vltr}')
    backends = {'vvp': cyc_vvp, 'csim': cyc_csim, 'csim_hier': cyc_hier, 'vltr': cyc_vltr}
    failed = [k for k, v in backends.items() if v >= 50000]
    if failed:
        print(f'  ERROR: did not halt: {", ".join(failed)}')
        return
    if len(set(backends.values())) > 1:
        print(f'  WARNING: cycle count mismatch!')
    print()

    results = []
    for name, prog in programs.items():
        print(f'--- {name} ({len(prog)} words) ---')

        # vvp
        run_vvp, ct_vvp, cl_vvp = compile_vvp(prog)
        t0 = time.perf_counter(); cyc_vvp = run_vvp(); exec_vvp = time.perf_counter() - t0
        cl_vvp()

        # csim (flat)
        run_c, ct_c, cl_c = compile_csim(ir, prog)
        t0 = time.perf_counter(); cyc_c = run_c(); exec_c = time.perf_counter() - t0
        cl_c()

        # csim (hier)
        run_h, ct_h, cl_h = compile_csim_hier(hier_ir, hier_reg, prog)
        t0 = time.perf_counter(); cyc_h = run_h(); exec_h = time.perf_counter() - t0
        cl_h()

        # Verilator
        run_vl, ct_vl, cl_vl = compile_verilator(module, prog)
        t0 = time.perf_counter(); cyc_vl = run_vl(); exec_vl = time.perf_counter() - t0
        cl_vl()

        print(f'  Cycles:  vvp={cyc_vvp}, csim={cyc_c}, csim_hier={cyc_h}, vltr={cyc_vl}')
        print(f'  Compile: vvp={ct_vvp:.2f}s, csim={ct_c:.2f}s, csim_hier={ct_h:.2f}s, vltr={ct_vl:.2f}s')
        print(f'  Execute: vvp={exec_vvp:.4f}s, csim={exec_c:.4f}s, csim_hier={exec_h:.4f}s, vltr={exec_vl:.4f}s')
        print()
        results.append((name, cyc_vvp, cyc_c, cyc_h, cyc_vl,
                         ct_vvp, exec_vvp, ct_c, exec_c, ct_h, exec_h, ct_vl, exec_vl))

    # Summary table
    W = 100
    print('=' * W)
    print(f'{"Program":<14} {"vvp":>8} {"csim":>8} {"hier":>8} {"vltr":>8}   '
          f'{"vvp":>8} {"csim":>8} {"hier":>8} {"vltr":>8}')
    print(f'{"":14} {"---cycles---":^35}   {"---exec time---":^35}')
    print('-' * W)
    for r in results:
        name, c_vvp, c_c, c_h, c_vl = r[0], r[1], r[2], r[3], r[4]
        e_vvp, e_c, e_h, e_vl = r[6], r[8], r[10], r[12]
        print(f'{name:<14} {c_vvp:>8} {c_c:>8} {c_h:>8} {c_vl:>8}   '
              f'{e_vvp:>7.4f}s {e_c:>7.4f}s {e_h:>7.4f}s {e_vl:>7.4f}s')
    print()
    print('Compile time:')
    print(f'{"Program":<14} {"vvp":>8} {"csim":>8} {"hier":>8} {"vltr":>8}')
    print('-' * 50)
    for r in results:
        print(f'{r[0]:<14} {r[5]:>7.2f}s {r[7]:>7.2f}s {r[9]:>7.2f}s {r[11]:>7.2f}s')


if __name__ == '__main__':
    main()
