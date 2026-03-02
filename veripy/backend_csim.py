"""Native C simulation backend: IR → C code emitter + compile/load wrapper.

Emits a standalone C file from a *flat, topo-sorted* IRModule.  The generated
code provides ``veripy_create/destroy/eval`` plus per-signal ``set/get``
functions, matching the VerilatorModel ctypes interface.
"""

import ctypes
import os
import shutil
import subprocess
import tempfile

from .ir import (
    Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock, IRModule,
    Port, WireDecl, RegDecl, MemDecl,
)


# ── C type helpers ───────────────────────────────────────────────────

def _ctype(width):
    """Return narrowest C unsigned type for *width* bits."""
    if width <= 8:
        return 'uint8_t'
    if width <= 16:
        return 'uint16_t'
    if width <= 32:
        return 'uint32_t'
    return 'uint64_t'


def _mask(width):
    """Return C mask literal for *width* bits."""
    if width >= 64:
        return '0xFFFFFFFFFFFFFFFFULL'
    return hex((1 << width) - 1) + 'ULL'


def _resolve_width(w, params):
    """Resolve a width that may be a param name to an int."""
    if isinstance(w, int):
        return w
    return params.get(w, 1)


# ── Signal width map ─────────────────────────────────────────────────

def _build_sig_widths(ir: IRModule) -> dict[str, int]:
    """Build name → width map for every signal in the module."""
    params = ir.params
    w = {}
    for p in ir.ports:
        w[p.name] = _resolve_width(p.width, params)
    for d in ir.wires:
        w[d.name] = _resolve_width(d.width, params)
    for d in ir.regs:
        w[d.name] = _resolve_width(d.width, params)
    for blk in ir.comb_blocks:
        for name, width in blk.locals.items():
            w[name] = _resolve_width(width, params)
    for blk in ir.seq_blocks:
        for name, width in blk.locals.items():
            w[name] = _resolve_width(width, params)
    return w


# ── Expression emitter ───────────────────────────────────────────────

def _expr(node, sig_w) -> str:
    """Emit a C expression string from an IR Expr node."""
    if isinstance(node, Const):
        v = node.value
        if v < 0:
            return f'((uint64_t)({v}))'
        return f'{v}ULL'
    if isinstance(node, Param):
        return str(node.name)
    if isinstance(node, Sig):
        return f's->{node.name}'
    if isinstance(node, BinOp):
        l, r = _expr(node.left, sig_w), _expr(node.right, sig_w)
        return f'({l} {node.op} {r})'
    if isinstance(node, UnaryOp):
        op = node.op
        if op == '!':
            return f'(!{_expr(node.operand, sig_w)})'
        return f'({op}{_expr(node.operand, sig_w)})'
    if isinstance(node, Compare):
        l, r = _expr(node.left, sig_w), _expr(node.right, sig_w)
        return f'({l} {node.op} {r})'
    if isinstance(node, BoolOp):
        parts = []
        for v in node.values:
            s = _expr(v, sig_w)
            if isinstance(v, BoolOp):
                s = f'({s})'
            parts.append(s)
        return f' {node.op} '.join(parts)
    if isinstance(node, Mux):
        s, t, f = (_expr(node.sel, sig_w), _expr(node.true_val, sig_w),
                   _expr(node.false_val, sig_w))
        return f'({s} ? {t} : {f})'
    if isinstance(node, Slice):
        sig = _expr(node.signal, sig_w)
        lo = _expr(node.lo, sig_w)
        if node.hi is None:
            return f'(({sig} >> {lo}) & 1ULL)'
        hi = _expr(node.hi, sig_w)
        return f'(({sig} >> {lo}) & ((1ULL << ({hi} - {lo} + 1ULL)) - 1ULL))'
    if isinstance(node, Index):
        return f's->{node.signal.name}[{_expr(node.idx, sig_w)}]'
    if isinstance(node, Concat):
        # MSB-first: parts[0] is MSB
        parts = node.parts
        if not parts:
            return '0ULL'
        result = _expr(parts[-1], sig_w)
        shift = _expr_width(parts[-1], sig_w)
        for p in reversed(parts[:-1]):
            pw = _expr_width(p, sig_w)
            result = f'(({_expr(p, sig_w)} << {shift}ULL) | {result})'
            shift += pw
        return result
    raise ValueError(f'Unknown IR expr: {node}')


def _expr_width(node, sig_w) -> int:
    """Estimate the bit-width of an expression (best-effort)."""
    if isinstance(node, Const):
        return max(node.value.bit_length(), 1) if node.value >= 0 else 32
    if isinstance(node, Sig):
        return sig_w.get(node.name, 32)
    if isinstance(node, Slice):
        if node.hi is None:
            return 1
        if isinstance(node.hi, Const) and isinstance(node.lo, Const):
            return node.hi.value - node.lo.value + 1
        return 32
    if isinstance(node, Index):
        # Memory read — width of the memory element
        if isinstance(node.signal, Sig):
            return sig_w.get(node.signal.name, 32)
        return 32
    if isinstance(node, Concat):
        return sum(_expr_width(p, sig_w) for p in node.parts)
    if isinstance(node, BinOp):
        return max(_expr_width(node.left, sig_w), _expr_width(node.right, sig_w))
    if isinstance(node, Mux):
        return max(_expr_width(node.true_val, sig_w),
                   _expr_width(node.false_val, sig_w))
    if isinstance(node, (Compare, BoolOp)):
        return 1
    if isinstance(node, UnaryOp):
        return _expr_width(node.operand, sig_w)
    return 32


# ── Statement emitter ────────────────────────────────────────────────

def _emit_stmt(stmt, lines, sig_w, indent=1):
    """Emit C statements from an IR Stmt node."""
    pad = '    ' * indent

    if isinstance(stmt, Assign):
        w = sig_w.get(stmt.target, 0)
        val = _expr(stmt.value, sig_w)
        if w and w < 64:
            lines.append(f'{pad}s->{stmt.target} = ({_ctype(w)})({val} & {_mask(w)});')
        else:
            lines.append(f'{pad}s->{stmt.target} = {val};')

    elif isinstance(stmt, SliceAssign):
        lo = _expr(stmt.lo, sig_w)
        hi = _expr(stmt.hi, sig_w)
        val = _expr(stmt.value, sig_w)
        # Clear bits [hi:lo], then set them
        lines.append(f'{pad}{{')
        lines.append(f'{pad}    uint64_t _lo = {lo};')
        lines.append(f'{pad}    uint64_t _hi = {hi};')
        lines.append(f'{pad}    uint64_t _w = _hi - _lo + 1;')
        lines.append(f'{pad}    uint64_t _mask = ((1ULL << _w) - 1) << _lo;')
        lines.append(f'{pad}    s->{stmt.target} = (s->{stmt.target} & ~_mask) | '
                     f'((({val}) << _lo) & _mask);')
        lines.append(f'{pad}}}')

    elif isinstance(stmt, MemWrite):
        val = _expr(stmt.data, sig_w)
        addr = _expr(stmt.addr, sig_w)
        lines.append(f'{pad}s->{stmt.mem}[{addr}] = {val};')

    elif isinstance(stmt, If):
        lines.append(f'{pad}if ({_expr(stmt.cond, sig_w)}) {{')
        for s in stmt.then_body:
            _emit_stmt(s, lines, sig_w, indent + 1)
        if stmt.else_body:
            if len(stmt.else_body) == 1 and isinstance(stmt.else_body[0], If):
                lines.append(f'{pad}}} else')
                _emit_stmt(stmt.else_body[0], lines, sig_w, indent)
            else:
                lines.append(f'{pad}}} else {{')
                for s in stmt.else_body:
                    _emit_stmt(s, lines, sig_w, indent + 1)
                lines.append(f'{pad}}}')
        else:
            lines.append(f'{pad}}}')

    elif isinstance(stmt, Case):
        lines.append(f'{pad}switch ({_expr(stmt.sel, sig_w)}) {{')
        for val, body in stmt.cases:
            lines.append(f'{pad}    case {_expr(val, sig_w)}:')
            for s in body:
                _emit_stmt(s, lines, sig_w, indent + 2)
            lines.append(f'{pad}        break;')
        if stmt.default:
            lines.append(f'{pad}    default:')
            for s in stmt.default:
                _emit_stmt(s, lines, sig_w, indent + 2)
            lines.append(f'{pad}        break;')
        lines.append(f'{pad}}}')


# ── Top-level C emitter ──────────────────────────────────────────────

def emit_c(ir: IRModule) -> str:
    """Emit C source from a flat, topo-sorted IRModule.

    The module should have been processed through ``flatten_ir`` and
    ``topo_sort_comb`` before calling this function.

    Returns:
        Complete C source string.
    """
    sig_w = _build_sig_widths(ir)
    lines = [
        '#include <stdint.h>',
        '#include <stdlib.h>',
        '#include <string.h>',
        '',
    ]

    # ── State struct ─────────────────────────────────────────────
    lines.append('typedef struct {')

    # Signals (ports, wires, regs)
    all_sigs = []
    for p in ir.ports:
        w = _resolve_width(p.width, ir.params)
        all_sigs.append((p.name, w))
    for d in ir.wires:
        w = _resolve_width(d.width, ir.params)
        all_sigs.append((d.name, w))
    for d in ir.regs:
        w = _resolve_width(d.width, ir.params)
        all_sigs.append((d.name, w))
    # Block locals
    for blk in ir.comb_blocks:
        for name, width in blk.locals.items():
            w = _resolve_width(width, ir.params)
            all_sigs.append((name, w))
    for blk in ir.seq_blocks:
        for name, width in blk.locals.items():
            w = _resolve_width(width, ir.params)
            all_sigs.append((name, w))

    seen_sigs = set()
    for name, w in all_sigs:
        if name not in seen_sigs:
            seen_sigs.add(name)
            lines.append(f'    {_ctype(w)} {name};')

    # Memory arrays
    for m in ir.mems:
        if isinstance(m, MemDecl):
            w = _resolve_width(m.width, ir.params)
            lines.append(f'    {_ctype(w)} {m.name}[{m.depth}];')

    # Previous values for edge detection
    clocks = set()
    for blk in ir.seq_blocks:
        for edge_kind, sig_name in blk.edges:
            clocks.add(sig_name)
    for clk in sorted(clocks):
        lines.append(f'    uint8_t _prev_{clk};')

    lines.append('} State;')
    lines.append('')

    # ── create / destroy ─────────────────────────────────────────
    lines.append('void* veripy_create(void) {')
    lines.append('    return calloc(1, sizeof(State));')
    lines.append('}')
    lines.append('')
    lines.append('void veripy_destroy(void* p) {')
    lines.append('    free(p);')
    lines.append('}')
    lines.append('')

    # ── eval ─────────────────────────────────────────────────────
    lines.append('void veripy_eval(void* p) {')
    lines.append('    State* s = (State*)p;')

    # Edge detection + sequential blocks
    # Group seq blocks by (edge_kind, clock)
    edge_blocks = {}
    for blk in ir.seq_blocks:
        for edge_kind, sig_name in blk.edges:
            edge_blocks.setdefault((edge_kind, sig_name), []).append(blk)

    for (edge_kind, clk), blocks in sorted(edge_blocks.items()):
        if edge_kind == 'posedge':
            cond = f's->{clk} && !s->_prev_{clk}'
        else:
            cond = f'!s->{clk} && s->_prev_{clk}'
        lines.append(f'    if ({cond}) {{')
        for blk in blocks:
            for stmt in blk.stmts:
                _emit_stmt(stmt, lines, sig_w, indent=2)
        lines.append('    }')

    # Combinational logic (topo-sorted: assigns first, then comb_blocks)
    for a in ir.assigns:
        w = sig_w.get(a.target, 0)
        val = _expr(a.value, sig_w)
        if w and w < 64:
            lines.append(f'    s->{a.target} = ({_ctype(w)})({val} & {_mask(w)});')
        else:
            lines.append(f'    s->{a.target} = {val};')

    for blk in ir.comb_blocks:
        for stmt in blk.stmts:
            _emit_stmt(stmt, lines, sig_w)

    # Update previous values
    for clk in sorted(clocks):
        lines.append(f'    s->_prev_{clk} = s->{clk};')

    lines.append('}')
    lines.append('')

    # ── Per-signal set/get ───────────────────────────────────────
    for p in ir.ports:
        w = _resolve_width(p.width, ir.params)
        if p.direction == 'input':
            lines.append(f'void veripy_set_{p.name}(void* p, uint64_t v) '
                         f'{{ ((State*)p)->{p.name} = ({_ctype(w)})(v & {_mask(w)}); }}')
        lines.append(f'uint64_t veripy_get_{p.name}(void* p) '
                     f'{{ return ((State*)p)->{p.name}; }}')
        lines.append('')

    return '\n'.join(lines) + '\n'


# ── Compile + load ───────────────────────────────────────────────────

def compile_module(module, module_name=None):
    """Compile a VeriPy Module to a CSimModel.

    Handles lowering, sub-module collection, flattening, and compilation.
    """
    from .signal import Signal, Interface
    from .lower import lower_module
    from .flatten import flatten_ir
    from .emit_verilog import _to_snake

    if module_name is None:
        module_name = type(module).__name__.lower()

    # Collect sub-module IRs into registry
    registry = {}
    def _collect(m, mname):
        if mname in registry:
            return
        factory = getattr(type(m), '_veripy_factory', None)
        fresh = factory() if factory else type(m)()
        for sn, sub in fresh._submodules().items():
            _collect(sub, _to_snake(type(sub).__name__))
        registry[mname] = lower_module(fresh, mname)

    from .module import Module as _Module
    for k in dir(module):
        v = getattr(module, k)
        if isinstance(v, _Module) and v is not module:
            _collect(v, _to_snake(type(v).__name__))

    top_ir = lower_module(module, module_name)
    flat_ir = flatten_ir(top_ir, registry) if top_ir.instances else top_ir

    return CSimModel(flat_ir)


class CSimModel:
    """Compile C source to shared lib and wrap with ctypes.

    Same API as VerilatorModel: set/get/eval/step/close.
    """

    def __init__(self, ir: IRModule, build_dir=None):
        from .flatten import topo_sort_comb

        self._ptr = None
        self._tmpdir = None
        self._lib = None

        if ir.instances:
            raise ValueError('IR must be flattened before CSimModel '
                             '(call flatten_ir first)')
        ir = topo_sort_comb(ir)

        self._signals = {}
        for p in ir.ports:
            w = _resolve_width(p.width, ir.params)
            self._signals[p.name] = (p.direction, w)

        c_src = emit_c(ir)

        own_tmpdir = build_dir is None
        if own_tmpdir:
            build_dir = tempfile.mkdtemp(prefix='veripy_csim_')
        self._tmpdir = build_dir if own_tmpdir else None

        c_path = os.path.join(build_dir, f'{ir.name}.c')
        with open(c_path, 'w') as f:
            f.write(c_src)

        ext = '.dylib' if os.uname().sysname == 'Darwin' else '.so'
        lib_path = os.path.join(build_dir, f'lib{ir.name}{ext}')

        cc = os.environ.get('CC', 'cc')
        flag = '-dynamiclib' if ext == '.dylib' else '-shared'
        r = subprocess.run(
            [cc, '-O2', '-fPIC', flag, '-o', lib_path, c_path],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f'C compilation failed:\n{r.stderr}')

        self._lib = ctypes.CDLL(lib_path)
        self._lib.veripy_create.restype = ctypes.c_void_p
        self._lib.veripy_destroy.argtypes = [ctypes.c_void_p]
        self._lib.veripy_eval.argtypes = [ctypes.c_void_p]
        self._ptr = self._lib.veripy_create()

        self._setters = {}
        self._getters = {}
        for name, (kind, width) in self._signals.items():
            if kind == 'input':
                fn = getattr(self._lib, f'veripy_set_{name}')
                fn.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
                self._setters[name] = fn
            fn = getattr(self._lib, f'veripy_get_{name}')
            fn.restype = ctypes.c_uint64
            fn.argtypes = [ctypes.c_void_p]
            self._getters[name] = fn

    def set(self, name, val):
        self._setters[name](self._ptr, val)

    def get(self, name):
        return self._getters[name](self._ptr)

    def eval(self):
        self._lib.veripy_eval(self._ptr)

    def step(self, clock_name, n=1):
        for _ in range(n):
            self.set(clock_name, 0)
            self.eval()
            self.set(clock_name, 1)
            self.eval()

    def close(self):
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
