"""Verilator co-simulation backend: compile Verilog → shared lib, drive via ctypes."""

import ctypes
import os
import shutil
import subprocess
import tempfile


def _has_verilator():
    """Check if verilator is available on PATH."""
    return shutil.which('verilator') is not None


def _generate_wrapper(module_name, signals):
    """Generate C++ wrapper that exports per-signal set/get functions.

    signals: dict of {name: (kind, width)} where kind is 'input' or 'output'.
    """
    lines = [
        f'#include "V{module_name}.h"',
        '#include "verilated.h"',
        '',
        'double sc_time_stamp() { return 0; }',
        '',
        'extern "C" {',
        '',
        'static VerilatedContext* _ctx;',
        '',
        'void* veripy_create() {',
        '    _ctx = new VerilatedContext;',
        f'    return new V{module_name}{{_ctx}};',
        '}',
        '',
        'void veripy_destroy(void* p) {',
        f'    delete static_cast<V{module_name}*>(p);',
        '    delete _ctx; _ctx = nullptr;',
        '}',
        '',
        'void veripy_eval(void* p) {',
        f'    static_cast<V{module_name}*>(p)->eval();',
        '}',
        '',
    ]

    for name, (kind, width) in sorted(signals.items()):
        ctype = 'uint8_t' if width <= 8 else (
                'uint16_t' if width <= 16 else (
                'uint32_t' if width <= 32 else 'uint64_t'))
        cast = f'static_cast<V{module_name}*>(p)'
        if kind == 'input':
            lines.append(f'void veripy_set_{name}(void* p, uint64_t v) '
                         f'{{ {cast}->{name} = static_cast<{ctype}>(v); }}')
        lines.append(f'uint64_t veripy_get_{name}(void* p) '
                     f'{{ return {cast}->{name}; }}')
        lines.append('')

    lines.append('}  // extern "C"')
    return '\n'.join(lines) + '\n'


def compile_verilator(verilog_src, module_name, signals, build_dir=None):
    """Compile Verilog source with Verilator and return a VerilatorModel.

    Args:
        verilog_src: Complete Verilog source (DUT + sub-modules).
        module_name: Top-level module name.
        signals: dict of {name: (kind, width)}.
        build_dir: Optional persistent build directory. If None, uses a temp dir.

    Returns:
        VerilatorModel instance.

    Raises:
        RuntimeError: If verilator is not installed or compilation fails.
    """
    if not _has_verilator():
        raise RuntimeError('verilator not found on PATH')

    own_tmpdir = build_dir is None
    if own_tmpdir:
        build_dir = tempfile.mkdtemp(prefix='veripy_verilator_')

    dut_path = os.path.join(build_dir, f'{module_name}.v')
    wrapper_path = os.path.join(build_dir, 'wrapper.cpp')
    obj_dir = os.path.join(build_dir, 'obj_dir')

    with open(dut_path, 'w') as f:
        f.write(verilog_src)
    with open(wrapper_path, 'w') as f:
        f.write(_generate_wrapper(module_name, signals))

    # Run verilator to generate C++ model
    r = subprocess.run(
        ['verilator', '--cc', dut_path, '--Mdir', obj_dir,
         '-CFLAGS', '-fPIC', '-Wno-fatal'],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'verilator failed:\n{r.stderr}')

    # Build the model objects
    r = subprocess.run(['make', '-C', obj_dir, '-f', f'V{module_name}.mk'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'make failed:\n{r.stderr}')

    # Compile wrapper + link into shared library
    verilator_root = subprocess.run(
        ['verilator', '--getenv', 'VERILATOR_ROOT'],
        capture_output=True, text=True).stdout.strip()
    include_dir = os.path.join(verilator_root, 'include')

    lib_name = f'libveripy_{module_name}.dylib' if os.uname().sysname == 'Darwin' \
               else f'libveripy_{module_name}.so'
    lib_path = os.path.join(build_dir, lib_name)

    # Collect all .o files from obj_dir
    obj_files = [os.path.join(obj_dir, f) for f in os.listdir(obj_dir)
                 if f.endswith('.o')]

    # Compile wrapper
    wrapper_obj = os.path.join(build_dir, 'wrapper.o')
    r = subprocess.run(
        ['c++', '-fPIC', '-shared' if os.uname().sysname != 'Darwin' else '-dynamiclib',
         '-I', obj_dir, '-I', include_dir, '-I', os.path.join(include_dir, 'vltstd'),
         wrapper_path] + obj_files + ['-o', lib_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'shared lib compilation failed:\n{r.stderr}')

    return VerilatorModel(lib_path, signals, build_dir if own_tmpdir else None)


def compile_module(module, module_name=None):
    """Compile a VeriPy Module with Verilator and return a VerilatorModel.

    Convenience wrapper that handles Verilog emission and signal collection.
    """
    from .signal import Signal, Interface
    from .emit_verilog import _to_snake

    if module_name is None:
        module_name = type(module).__name__.lower()

    # Collect signals
    signals = {}
    for k in dir(module):
        v = getattr(module, k)
        if isinstance(v, Signal) and v._kind in ('input', 'output'):
            signals[k] = (v._kind, v.width)
        elif isinstance(v, Interface):
            for sn in v._signals():
                sig = getattr(v, sn)
                if sig._kind in ('input', 'output'):
                    signals[f'{k}_{sn}'] = (sig._kind, sig.width)

    # Emit Verilog (DUT + sub-modules)
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
    for sn, sub in module._submodules().items():
        _collect(sub, _to_snake(type(sub).__name__))
    parts.append(module.to_verilog(module_name))
    verilog_src = '\n\n'.join(parts)

    return compile_verilator(verilog_src, module_name, signals)


class VerilatorModel:
    """Python wrapper around a Verilator-compiled shared library.

    Provides set/get/eval interface via ctypes for driving simulation
    from Python at near-native speed.
    """

    def __init__(self, lib_path, signals, tmpdir=None):
        self._lib = ctypes.CDLL(lib_path)
        self._tmpdir = tmpdir  # cleaned up on del if we own it
        self._signals = signals

        self._lib.veripy_create.restype = ctypes.c_void_p
        self._lib.veripy_destroy.argtypes = [ctypes.c_void_p]
        self._lib.veripy_eval.argtypes = [ctypes.c_void_p]

        self._ptr = self._lib.veripy_create()

        self._setters = {}
        self._getters = {}
        for name, (kind, width) in signals.items():
            if kind == 'input':
                fn = getattr(self._lib, f'veripy_set_{name}')
                fn.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
                self._setters[name] = fn
            fn = getattr(self._lib, f'veripy_get_{name}')
            fn.restype = ctypes.c_uint64
            fn.argtypes = [ctypes.c_void_p]
            self._getters[name] = fn

    def set(self, name, val):
        """Set an input signal value."""
        self._setters[name](self._ptr, val)

    def get(self, name):
        """Read a signal value."""
        return self._getters[name](self._ptr)

    def eval(self):
        """Evaluate combinational logic (one delta cycle)."""
        self._lib.veripy_eval(self._ptr)

    def step(self, clock_name, n=1):
        """Toggle clock n times (rising + falling edge each)."""
        for _ in range(n):
            self.set(clock_name, 0)
            self.eval()
            self.set(clock_name, 1)
            self.eval()

    def close(self):
        """Release the model and clean up."""
        if self._ptr:
            self._lib.veripy_destroy(self._ptr)
            self._ptr = None
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ── Native Verilator testbench ───────────────────────────────────────

def compile_verilator_bench(module, tb_ir, module_name=None):
    """Compile Verilator model + native C++ testbench → single .so with run_bench().

    Returns (run_fn, compile_time, cleanup_fn).
    """
    import time as _time
    from .signal import Signal, Interface
    from .emit_verilog import _to_snake
    from .ir import (Const, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
                     Assign, If, Delay, ForLoop, Repeat, Disable, Display, Finish)

    if not _has_verilator():
        raise RuntimeError('verilator not found on PATH')

    if module_name is None:
        module_name = type(module).__name__.lower()

    t0 = _time.perf_counter()

    # Collect signals
    signals = {}
    for k in dir(module):
        v = getattr(module, k)
        if isinstance(v, Signal) and v._kind in ('input', 'output'):
            signals[k] = (v._kind, v.width)
        elif isinstance(v, Interface):
            for sn in v._signals():
                sig = getattr(v, sn)
                if sig._kind in ('input', 'output'):
                    signals[f'{k}_{sn}'] = (sig._kind, sig.width)

    # Emit Verilog
    parts = []
    seen = set()
    def _collect_v(m, mname):
        if mname in seen:
            return
        seen.add(mname)
        factory = getattr(type(m), '_veripy_factory', None)
        fresh = factory() if factory else type(m)()
        for sn, sub in fresh._submodules().items():
            _collect_v(sub, _to_snake(type(sub).__name__))
        parts.append(fresh.to_verilog(mname))
    for sn, sub in module._submodules().items():
        _collect_v(sub, _to_snake(type(sub).__name__))
    parts.append(module.to_verilog(module_name))
    verilog_src = '\n\n'.join(parts)

    build_dir = tempfile.mkdtemp(prefix='veripy_vltr_bench_')
    dut_path = os.path.join(build_dir, f'{module_name}.v')
    obj_dir = os.path.join(build_dir, 'obj_dir')

    with open(dut_path, 'w') as f:
        f.write(verilog_src)

    # Verilate
    r = subprocess.run(
        ['verilator', '--cc', dut_path, '--Mdir', obj_dir,
         '-CFLAGS', '-fPIC', '-Wno-fatal'],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'verilator failed:\n{r.stderr}')

    r = subprocess.run(['make', '-C', obj_dir, '-f', f'V{module_name}.mk'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'make failed:\n{r.stderr}')

    # Extract clock name and half-period from always block
    clock_name = 'clock'
    half_period = 10
    if tb_ir.always_blocks:
        for s in tb_ir.always_blocks[0].stmts:
            if isinstance(s, Assign):
                clock_name = s.target
            if isinstance(s, Delay):
                if isinstance(s.value, Const):
                    half_period = s.value.value
                elif isinstance(s.value, BinOp) and s.value.op == '*':
                    l = s.value.left.value if isinstance(s.value.left, Const) else None
                    r_ = s.value.right.value if isinstance(s.value.right, Const) else None
                    if l is not None and r_ is not None:
                        half_period = l * r_
                break

    # Collect model signal names
    model_sigs = set(signals.keys())

    # Generate C++ testbench
    cls = f'V{module_name}'

    def _tb_expr(node):
        if isinstance(node, Const):
            v = node.value
            return f'((uint64_t)({v}))' if v < 0 else f'{v}ULL'
        if isinstance(node, Sig):
            if node.name in locals_set:
                return node.name
            return f'dut.{node.name}'
        if isinstance(node, BinOp):
            return f'({_tb_expr(node.left)} {node.op} {_tb_expr(node.right)})'
        if isinstance(node, UnaryOp):
            if node.op == '!':
                return f'(!{_tb_expr(node.operand)})'
            return f'({node.op}{_tb_expr(node.operand)})'
        if isinstance(node, Compare):
            return f'({_tb_expr(node.left)} {node.op} {_tb_expr(node.right)})'
        if isinstance(node, BoolOp):
            return f' {node.op} '.join(_tb_expr(v) for v in node.values)
        if isinstance(node, Mux):
            return f'({_tb_expr(node.sel)} ? {_tb_expr(node.true_val)} : {_tb_expr(node.false_val)})'
        raise ValueError(f'Verilator TB: unsupported expr: {node}')

    # Collect locals
    locals_set = set()
    def _collect_locals(stmts):
        for s in stmts:
            if isinstance(s, ForLoop):
                locals_set.add(s.var)
                _collect_locals(s.body)
            elif isinstance(s, Repeat):
                _collect_locals(s.body)
            elif isinstance(s, If):
                _collect_locals(s.then_body)
                if s.else_body:
                    _collect_locals(s.else_body)
            elif isinstance(s, Assign) and s.target not in model_sigs:
                locals_set.add(s.target)
    for blk in tb_ir.initial_blocks:
        _collect_locals(blk.stmts)

    lines = [
        f'#include "{cls}.h"',
        '#include "verilated.h"',
        '',
        'double sc_time_stamp() { return 0; }',
        '',
        'extern "C" {',
        '',
        f'static void _step({cls}& dut, int time_units) {{',
        f'    int n = time_units / {half_period};',
        f'    for (int _i = 0; _i < n; _i++) {{',
        f'        dut.{clock_name} ^= 1;',
        f'        dut.eval();',
        f'    }}',
        f'}}',
        '',
        'uint64_t run_bench() {',
        '    VerilatedContext ctx;',
        f'    {cls} dut{{&ctx}};',
    ]

    for v in sorted(locals_set):
        lines.append(f'    uint64_t {v} = 0;')

    def _tb_stmt(stmt, indent=1):
        pad = '    ' * indent
        if isinstance(stmt, Assign):
            val = _tb_expr(stmt.value)
            if stmt.target in locals_set:
                lines.append(f'{pad}{stmt.target} = {val};')
            else:
                lines.append(f'{pad}dut.{stmt.target} = {val};')
        elif isinstance(stmt, Delay):
            lines.append(f'{pad}_step(dut, {_tb_expr(stmt.value)});')
        elif isinstance(stmt, If):
            lines.append(f'{pad}if ({_tb_expr(stmt.cond)}) {{')
            for s in stmt.then_body:
                _tb_stmt(s, indent + 1)
            if stmt.else_body:
                lines.append(f'{pad}}} else {{')
                for s in stmt.else_body:
                    _tb_stmt(s, indent + 1)
            lines.append(f'{pad}}}')
        elif isinstance(stmt, ForLoop):
            lines.append(f'{pad}for ({stmt.var} = {_tb_expr(stmt.start)}; '
                         f'{stmt.var} < {_tb_expr(stmt.stop)}; {stmt.var}++) {{')
            for s in stmt.body:
                _tb_stmt(s, indent + 1)
            lines.append(f'{pad}}}')
            if stmt.label:
                lines.append(f'{pad}{stmt.label}_end: ;')
        elif isinstance(stmt, Repeat):
            cvar = f'_rep{id(stmt) % 10000}'
            lines.append(f'{pad}for (int {cvar} = 0; {cvar} < {_tb_expr(stmt.count)}; {cvar}++) {{')
            for s in stmt.body:
                _tb_stmt(s, indent + 1)
            lines.append(f'{pad}}}')
            if stmt.label:
                lines.append(f'{pad}{stmt.label}_end: ;')
        elif isinstance(stmt, Disable):
            lines.append(f'{pad}goto {stmt.label}_end;')
        elif isinstance(stmt, (Display, Finish)):
            pass

    for blk in tb_ir.initial_blocks:
        for s in blk.stmts:
            _tb_stmt(s)

    lines.append('    return 0;')
    lines.append('}')
    lines.append('')
    lines.append('}  // extern "C"')

    tb_src = '\n'.join(lines) + '\n'
    tb_path = os.path.join(build_dir, 'bench.cpp')
    with open(tb_path, 'w') as f:
        f.write(tb_src)

    # Compile
    verilator_root = subprocess.run(
        ['verilator', '--getenv', 'VERILATOR_ROOT'],
        capture_output=True, text=True).stdout.strip()
    include_dir = os.path.join(verilator_root, 'include')

    obj_files = [os.path.join(obj_dir, f) for f in os.listdir(obj_dir)
                 if f.endswith('.o')]

    ext = '.dylib' if os.uname().sysname == 'Darwin' else '.so'
    lib_path = os.path.join(build_dir, f'libbench{ext}')
    flag = '-dynamiclib' if ext == '.dylib' else '-shared'

    r = subprocess.run(
        ['c++', '-O2', '-fPIC', flag,
         '-I', obj_dir, '-I', include_dir, '-I', os.path.join(include_dir, 'vltstd'),
         tb_path] + obj_files + ['-o', lib_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'Verilator bench compilation failed:\n{r.stderr}\n\nSource:\n{tb_src}')

    compile_t = _time.perf_counter() - t0

    lib = ctypes.CDLL(lib_path)
    lib.run_bench.restype = ctypes.c_uint64

    def run():
        lib.run_bench()

    def cleanup():
        shutil.rmtree(build_dir, ignore_errors=True)

    return run, compile_t, cleanup
