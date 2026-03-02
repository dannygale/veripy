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
         '-CFLAGS', '-fPIC'],
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
         wrapper_path] + obj_files + [
         os.path.join(include_dir, 'verilated.cpp'),
         '-o', lib_path],
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
