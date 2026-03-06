#!/usr/bin/env python3
"""CPU benchmark: csim vs Verilator running RV32I programs.

Assembles programs using instruction encoders, loads them into the CPU's
memory, and benchmarks simulation performance.
"""

import sys, os, time, tempfile, subprocess, shutil, ctypes, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'vhdl_cpu', 'isa', 'v3'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'vhdl_cpu', 'lib', 'v3'))

# ── RV32I instruction encoders ──────────────────────────────────────

def _i(imm, rs1, f3, rd, op):
    return ((imm & 0xFFF) << 20 | (rs1 & 0x1F) << 15 |
            (f3 & 7) << 12 | (rd & 0x1F) << 7 | (op & 0x7F))

def _r(f7, rs2, rs1, f3, rd, op):
    return ((f7 & 0x7F) << 25 | (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15 |
            (f3 & 7) << 12 | (rd & 0x1F) << 7 | (op & 0x7F))

def _s(imm, rs2, rs1, f3, op):
    return (((imm >> 5) & 0x7F) << 25 | (rs2 & 0x1F) << 20 |
            (rs1 & 0x1F) << 15 | (f3 & 7) << 12 |
            (imm & 0x1F) << 7 | (op & 0x7F))

def _b(imm, rs2, rs1, f3):
    return (((imm >> 12) & 1) << 31 | ((imm >> 5) & 0x3F) << 25 |
            (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15 | (f3 & 7) << 12 |
            ((imm >> 1) & 0xF) << 8 | ((imm >> 11) & 1) << 7 | 0x63)

def _u(imm20, rd, op):
    return ((imm20 & 0xFFFFF) << 12 | (rd & 0x1F) << 7 | (op & 0x7F))

def _j(imm, rd):
    return (((imm >> 20) & 1) << 31 | ((imm >> 1) & 0x3FF) << 21 |
            ((imm >> 11) & 1) << 20 | ((imm >> 12) & 0xFF) << 12 |
            (rd & 0x1F) << 7 | 0x6F)

def ADDI(rd, rs1, imm):  return _i(imm & 0xFFF, rs1, 0, rd, 0x13)
def SLTI(rd, rs1, imm):  return _i(imm & 0xFFF, rs1, 2, rd, 0x13)
def ANDI(rd, rs1, imm):  return _i(imm & 0xFFF, rs1, 7, rd, 0x13)
def ORI(rd, rs1, imm):   return _i(imm & 0xFFF, rs1, 6, rd, 0x13)
def XORI(rd, rs1, imm):  return _i(imm & 0xFFF, rs1, 4, rd, 0x13)
def SLLI(rd, rs1, sh):   return _r(0, sh, rs1, 1, rd, 0x13)
def SRLI(rd, rs1, sh):   return _r(0, sh, rs1, 5, rd, 0x13)
def SRAI(rd, rs1, sh):   return _r(0x20, sh, rs1, 5, rd, 0x13)
def ADD(rd, rs1, rs2):   return _r(0, rs2, rs1, 0, rd, 0x33)
def SUB(rd, rs1, rs2):   return _r(0x20, rs2, rs1, 0, rd, 0x33)
def SLT(rd, rs1, rs2):   return _r(0, rs2, rs1, 2, rd, 0x33)
def AND(rd, rs1, rs2):   return _r(0, rs2, rs1, 7, rd, 0x33)
def OR(rd, rs1, rs2):    return _r(0, rs2, rs1, 6, rd, 0x33)
def XOR(rd, rs1, rs2):   return _r(0, rs2, rs1, 4, rd, 0x33)
def SW(rs1, rs2, imm):   return _s(imm & 0xFFF, rs2, rs1, 2, 0x23)
def LW(rd, rs1, imm):    return _i(imm & 0xFFF, rs1, 2, rd, 0x03)
def LUI(rd, imm20):      return _u(imm20, rd, 0x37)
def BEQ(rs1, rs2, imm):  return _b(imm, rs2, rs1, 0)
def BNE(rs1, rs2, imm):  return _b(imm, rs2, rs1, 1)
def BLT(rs1, rs2, imm):  return _b(imm, rs2, rs1, 4)
def BGE(rs1, rs2, imm):  return _b(imm, rs2, rs1, 5)
def JAL(rd, imm):        return _j(imm, rd)
def JALR(rd, rs1, imm):  return _i(imm & 0xFFF, rs1, 0, rd, 0x67)
def NOP():               return ADDI(0, 0, 0)
def WFI():               return 0x10500073


# ── Benchmark programs ──────────────────────────────────────────────

def _assemble(asm_src):
    """Assemble RV32I source with the toolchain, return list of uint32 words."""
    with tempfile.TemporaryDirectory() as tmp:
        asm_path = os.path.join(tmp, 'prog.S')
        elf_path = os.path.join(tmp, 'prog.elf')
        bin_path = os.path.join(tmp, 'prog.bin')
        ld_path = os.path.join(os.path.dirname(__file__), '..', '..',
                               'vhdl_cpu', 'isa', 'v2', 'programs', 'link.ld')
        with open(asm_path, 'w') as f:
            f.write(asm_src)
        cc = 'riscv64-elf-gcc'
        objcopy = 'riscv64-elf-objcopy'
        r = subprocess.run(
            [cc, '-march=rv32i', '-mabi=ilp32', '-nostdlib', '-T', ld_path,
             '-o', elf_path, asm_path],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f'Assembly failed:\n{r.stderr}')
        subprocess.run([objcopy, '-O', 'binary', elf_path, bin_path],
                       capture_output=True, text=True, check=True)
        with open(bin_path, 'rb') as f:
            data = f.read()
    # Pad to word boundary
    while len(data) % 4:
        data += b'\x00'
    return list(struct.unpack(f'<{len(data)//4}I', data))


def prog_fibonacci(n=500):
    """Fibonacci: compute fib(n) mod 2^32. ALU-bound, minimal memory."""
    return _assemble(f"""
    .section .text.init
    .globl _start
_start:
    addi x1, x0, 0
    addi x2, x0, 1
    li   x4, {n}
    addi x5, x0, 0
.Lloop:
    beq  x5, x4, .Ldone
    add  x3, x1, x2
    add  x1, x2, x0
    add  x2, x3, x0
    addi x5, x5, 1
    j    .Lloop
.Ldone:
    .word 0x10500073
""")


def prog_bubblesort(count=64):
    """Bubblesort: sort `count` descending values. Memory-intensive."""
    # Initialize array at 0x200 with descending values, then sort
    return _assemble(f"""
    .section .text.init
    .globl _start
_start:
    # Initialize array at 0x200 with descending values
    li   x10, 0x200         # base
    li   x11, {count}       # count
    add  x12, x11, x0       # val = count (descending)
    add  x13, x10, x0       # ptr = base
.Linit:
    beq  x12, x0, .Lsort
    sw   x12, 0(x13)
    addi x13, x13, 4
    addi x12, x12, -1
    j    .Linit

.Lsort:
    # Bubble sort outer loop
    addi x20, x0, 0        # swapped = 0
    addi x14, x0, 0        # i = 0
    addi x15, x11, -1      # limit = count - 1
.Linner:
    beq  x14, x15, .Lcheck
    slli x16, x14, 2       # offset = i * 4
    add  x17, x10, x16     # addr = base + offset
    lw   x18, 0(x17)       # a = arr[i]
    lw   x19, 4(x17)       # b = arr[i+1]
    bge  x19, x18, .Lnoswap
    sw   x19, 0(x17)       # swap
    sw   x18, 4(x17)
    addi x20, x0, 1        # swapped = 1
.Lnoswap:
    addi x14, x14, 4
    addi x15, x15, -1      # shrink limit (optimization)
    j    .Linner
.Lcheck:
    bne  x20, x0, .Lsort
    .word 0x10500073
""")


def prog_sieve(limit=200):
    """Sieve of Eratosthenes up to `limit`. Mixed ALU + memory."""
    return _assemble(f"""
    .section .text.init
    .globl _start
_start:
    # Sieve array at 0x200, one byte per word (wasteful but simple)
    li   x10, 0x200         # base
    li   x11, {limit}       # limit

    # Initialize: mark all as prime (1)
    addi x12, x0, 0
.Linit:
    bge  x12, x11, .Lsieve
    slli x13, x12, 2
    add  x13, x10, x13
    addi x14, x0, 1
    sw   x14, 0(x13)
    addi x12, x12, 1
    j    .Linit

.Lsieve:
    addi x15, x0, 2        # p = 2
.Louter:
    bge  x15, x11, .Lcount
    # Check if p is prime
    slli x13, x15, 2
    add  x13, x10, x13
    lw   x14, 0(x13)
    beq  x14, x0, .Lnextp
    # Mark multiples of p
    add  x16, x15, x15     # j = 2*p
.Lmark:
    bge  x16, x11, .Lnextp
    slli x13, x16, 2
    add  x13, x10, x13
    sw   x0, 0(x13)         # not prime
    add  x16, x16, x15      # j += p
    j    .Lmark
.Lnextp:
    addi x15, x15, 1
    j    .Louter

.Lcount:
    # Count primes into x1
    addi x1, x0, 0
    addi x12, x0, 2
.Lcnt:
    bge  x12, x11, .Ldone
    slli x13, x12, 2
    add  x13, x10, x13
    lw   x14, 0(x13)
    beq  x14, x0, .Lskip
    addi x1, x1, 1
.Lskip:
    addi x12, x12, 1
    j    .Lcnt
.Ldone:
    .word 0x10500073
""")


# ── Build infrastructure ────────────────────────────────────────────

def _build_cpu_ir():
    """Build flat, optimized IR for the CPU."""
    from cpu import cpu
    from veripy.lower import lower_module
    from veripy.flatten import flatten_ir, topo_sort_comb
    from veripy.backend_csim import _inline_cont_assigns
    from veripy.emit_verilog import _to_snake
    from veripy.module import Module as _Module

    m = cpu()
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
    top_ir = lower_module(m, 'cpu')
    flat = flatten_ir(top_ir, registry)
    flat = topo_sort_comb(flat)
    flat = _inline_cont_assigns(flat)
    return m, flat


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
    print(f'  {len(ir.comb_blocks)} comb, {len(ir.seq_blocks)} seq, '
          f'{sum(1 for m in ir.mems)} mems\n')

    programs = {
        'fibonacci(500)': prog_fibonacci(500),
        'bubblesort(64)': prog_bubblesort(64),
        'sieve(200)':     prog_sieve(200),
    }

    # Quick correctness check
    print('Correctness check (fib(20) = 10946)...')
    tiny = prog_fibonacci(20)
    run_v, _, cl_v = compile_vvp(tiny, max_cycles=10000)
    cyc_vvp = run_v(); cl_v()
    run_c, _, cl_c = compile_csim(ir, tiny, max_cycles=10000)
    cyc_csim = run_c(); cl_c()
    run_vl, _, cl_vl = compile_verilator(module, tiny, max_cycles=10000)
    cyc_vltr = run_vl(); cl_vl()
    print(f'  vvp={cyc_vvp} cycles, csim={cyc_csim} cycles, vltr={cyc_vltr} cycles')
    if cyc_vvp >= 10000 or cyc_csim >= 10000 or cyc_vltr >= 10000:
        print('  ERROR: one or more backends did not halt!')
        return
    if not (cyc_vvp == cyc_vltr):
        print(f'  WARNING: cycle count mismatch vvp={cyc_vvp} vltr={cyc_vltr}')
    print()

    results = []
    for name, prog in programs.items():
        print(f'--- {name} ({len(prog)} words) ---')

        # vvp
        run_vvp, ct_vvp, cl_vvp = compile_vvp(prog)
        t0 = time.perf_counter()
        cyc_vvp = run_vvp()
        exec_vvp = time.perf_counter() - t0
        cl_vvp()

        # csim
        run_c, ct_c, cl_c = compile_csim(ir, prog)
        t0 = time.perf_counter()
        cyc_c = run_c()
        exec_c = time.perf_counter() - t0
        cl_c()

        # Verilator
        run_vl, ct_vl, cl_vl = compile_verilator(module, prog)
        t0 = time.perf_counter()
        cyc_vl = run_vl()
        exec_vl = time.perf_counter() - t0
        cl_vl()

        print(f'  Cycles: vvp={cyc_vvp}, csim={cyc_c}, vltr={cyc_vl}')
        print(f'  Compile: vvp={ct_vvp:.2f}s, csim={ct_c:.2f}s, vltr={ct_vl:.2f}s')
        print(f'  Execute: vvp={exec_vvp:.4f}s, csim={exec_c:.4f}s, vltr={exec_vl:.4f}s')
        total_vvp = ct_vvp + exec_vvp
        total_c = ct_c + exec_c
        total_vl = ct_vl + exec_vl
        print(f'  Total:   vvp={total_vvp:.2f}s, csim={total_c:.2f}s, vltr={total_vl:.2f}s')
        fastest = min(total_vvp, total_c, total_vl)
        print(f'  Speedup: vvp={total_vvp/fastest:.1f}x, '
              f'csim={total_c/fastest:.1f}x, vltr={total_vl/fastest:.1f}x')
        print()
        results.append((name, cyc_vvp, ct_vvp, exec_vvp, ct_c, exec_c, ct_vl, exec_vl))

    # Summary table
    print('=' * 90)
    print(f'{"Program":<20} {"Cycles":>7} '
          f'{"vvp":>12} {"csim":>12} {"vltr":>12} {"csim speedup":>12}')
    print('-' * 90)
    for name, cyc, ct_vvp, e_vvp, ct_c, e_c, ct_vl, e_vl in results:
        t_vvp = ct_vvp + e_vvp
        t_c = ct_c + e_c
        t_vl = ct_vl + e_vl
        print(f'{name:<20} {cyc:>7} '
              f'{t_vvp:>10.2f}s {t_c:>10.2f}s {t_vl:>10.2f}s '
              f'{t_vl/t_c:>10.1f}x')
    print()
    print('Exec-only (no compile):')
    print(f'{"Program":<20} {"vvp exec":>10} {"csim exec":>10} {"vltr exec":>10} '
          f'{"vvp/csim":>8} {"vltr/csim":>9}')
    print('-' * 90)
    for name, cyc, ct_vvp, e_vvp, ct_c, e_c, ct_vl, e_vl in results:
        print(f'{name:<20} {e_vvp:>9.4f}s {e_c:>9.4f}s {e_vl:>9.4f}s '
              f'{e_vvp/e_c:>7.1f}x {e_vl/e_c:>8.1f}x')


if __name__ == '__main__':
    main()
