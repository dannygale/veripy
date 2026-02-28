"""WebGPU backend: IRModule → WGSL compute shader for GPU simulation.

Generates a compute shader that evaluates one simulation cycle per invocation.
Each GPU thread simulates an independent instance of the design with its own
stimulus, enabling batch-parallel verification.

Limitations (v1):
  - All signal widths must be concrete integers (no unresolved parameters)
  - No sub-module instances (flatten hierarchy first)
  - No Mem arrays
  - No Concat expressions (need width inference)
  - Max signal width: 32 bits
"""

from .ir import (
    Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock, IRModule,
)


def emit_wgsl(ir: IRModule, params: dict | None = None) -> tuple[str, list[str]]:
    """Generate WGSL compute shader from IRModule.

    Returns (wgsl_source, signal_names) where signal_names gives the
    struct field order so the host can map buffers correctly.
    """
    params = params or ir.params
    sigs = _collect_signals(ir, params)  # ordered list of (name, width)
    sig_w = {name: w for name, w in sigs}
    inputs = [p.name for p in ir.ports if p.direction == 'input']

    # All struct fields get s_ prefix to avoid WGSL keyword collisions
    def f(name: str) -> str:
        return f's_{name}'

    lines: list[str] = []

    # -- State struct --
    lines.append('struct State {')
    for name, _w in sigs:
        lines.append(f'    {f(name)}: u32,')
    # pad to multiple of 4 fields for safe alignment
    pad = (4 - len(sigs) % 4) % 4
    for i in range(pad):
        lines.append(f'    _pad{i}: u32,')
    lines.append('};')
    lines.append('')

    # -- Buffers --
    lines.append('@group(0) @binding(0) var<storage, read>       stim:  array<State>;')
    lines.append('@group(0) @binding(1) var<storage, read_write> state: array<State>;')
    lines.append('')

    # -- mask helper --
    lines.append('fn wmask(val: u32, w: u32) -> u32 {')
    lines.append('    if (w >= 32u) { return val; }')
    lines.append('    return val & ((1u << w) - 1u);')
    lines.append('}')
    lines.append('')

    # -- eval_comb --
    lines.append('fn eval_comb(s: ptr<function, State>) {')
    for a in ir.assigns:
        w = sig_w.get(a.target, 32)
        lines.append(f'    (*s).{f(a.target)} = wmask({_expr(a.value, "(*s)", f)}, {w}u);')
    for blk in ir.comb_blocks:
        for st in blk.stmts:
            _emit_stmt(st, lines, sig_w, '(*s)', '(*s)', f, indent=1)
    lines.append('}')
    lines.append('')

    # -- eval_seq --
    lines.append('fn eval_seq(s: ptr<function, State>, snap: State, prev: State) {')
    for blk in ir.seq_blocks:
        conds = []
        for kind, sig in blk.edges:
            if kind == 'posedge':
                conds.append(f'((*s).{f(sig)} != 0u && prev.{f(sig)} == 0u)')
            else:
                conds.append(f'((*s).{f(sig)} == 0u && prev.{f(sig)} != 0u)')
        guard = ' || '.join(conds)
        lines.append(f'    if ({guard}) {{')
        for st in blk.stmts:
            _emit_stmt(st, lines, sig_w, '(*s)', 'snap', f, indent=2)
        lines.append('    }')
    lines.append('}')
    lines.append('')

    # -- main entry point --
    lines.append('@compute @workgroup_size(64)')
    lines.append('fn main(@builtin(global_invocation_id) gid: vec3<u32>) {')
    lines.append('    let idx = gid.x;')
    lines.append('    if (idx >= arrayLength(&state)) { return; }')
    lines.append('    let prev = state[idx];')
    lines.append('    var s = prev;')
    for inp in inputs:
        lines.append(f'    s.{f(inp)} = stim[idx].{f(inp)};')
    lines.append('    eval_comb(&s);')
    lines.append('    let snap = s;')
    lines.append('    eval_seq(&s, snap, prev);')
    lines.append('    eval_comb(&s);')
    lines.append('    state[idx] = s;')
    lines.append('}')

    sig_names = [name for name, _w in sigs]
    return '\n'.join(lines), sig_names


# ── Signal collection ────────────────────────────────────────────────

def _collect_signals(ir: IRModule, params: dict) -> list[tuple[str, int]]:
    """Return ordered (name, concrete_width) for every signal."""
    sigs: list[tuple[str, int]] = []
    seen: set[str] = set()

    def _add(name, w):
        if name in seen:
            return
        seen.add(name)
        sigs.append((name, _resolve_width(w, params)))

    for p in ir.ports:
        _add(p.name, p.width)
    for r in ir.regs:
        _add(r.name, r.width)
    for w in ir.wires:
        _add(w.name, w.width)
    # locals declared in comb/seq blocks
    for blk in ir.comb_blocks:
        for name, w in blk.locals.items():
            _add(name, w)
    for blk in ir.seq_blocks:
        for name, w in blk.locals.items():
            _add(name, w)
    return sigs


def _resolve_width(w, params: dict) -> int:
    if isinstance(w, int):
        return w
    # Simple param expression: try eval with params as namespace
    try:
        return int(eval(str(w), {}, params))
    except Exception:
        raise ValueError(f'Cannot resolve width expression: {w!r} with params {params}')


# ── Expression emission ──────────────────────────────────────────────

def _expr(node, src: str, f) -> str:
    """Emit WGSL expression. `src` is the read source ('(*s)' or 'snap'). `f` prefixes signal names."""
    if isinstance(node, Const):
        return f'{node.value & 0xFFFFFFFF}u'

    if isinstance(node, (Param, Sig)):
        name = node.name if isinstance(node, Sig) else node.name
        return f'{src}.{f(name)}'

    if isinstance(node, BinOp):
        l, r = _expr(node.left, src, f), _expr(node.right, src, f)
        return f'({l} {node.op} {r})'

    if isinstance(node, UnaryOp):
        inner = _expr(node.operand, src, f)
        if node.op == '!':
            return f'select(1u, 0u, {inner} != 0u)'
        return f'{node.op}{inner}'  # ~ works in WGSL

    if isinstance(node, Compare):
        l, r = _expr(node.left, src, f), _expr(node.right, src, f)
        return f'select(0u, 1u, {l} {node.op} {r})'

    if isinstance(node, BoolOp):
        parts = [f'{_expr(v, src, f)} != 0u' for v in node.values]
        joined = f' {node.op} '.join(parts)
        return f'select(0u, 1u, {joined})'

    if isinstance(node, Mux):
        t = _expr(node.true_val, src, f)
        fv = _expr(node.false_val, src, f)
        c = _expr(node.sel, src, f)
        return f'select({fv}, {t}, {c} != 0u)'

    if isinstance(node, Slice):
        sig = _expr(node.signal, src, f)
        if isinstance(node.hi, Const) and isinstance(node.lo, Const):
            lo, hi = node.lo.value, node.hi.value
            w = hi - lo + 1
            if lo == 0:
                return f'({sig} & {(1 << w) - 1}u)'
            return f'(({sig} >> {lo}u) & {(1 << w) - 1}u)'
        lo = _expr(node.lo, src, f)
        hi = _expr(node.hi, src, f)
        return f'(({sig} >> {lo}) & ((1u << ({hi} - {lo} + 1u)) - 1u))'

    if isinstance(node, Index):
        sig = _expr(node.signal, src, f)
        idx = _expr(node.idx, src, f)
        return f'(({sig} >> {idx}) & 1u)'

    if isinstance(node, Concat):
        raise NotImplementedError('Concat not yet supported in WGSL backend')

    raise ValueError(f'Unknown IR expr: {node}')


# ── Statement emission ───────────────────────────────────────────────

def _emit_stmt(stmt, lines: list[str], sig_w: dict, dst: str, src: str, f, indent: int):
    pad = '    ' * indent

    if isinstance(stmt, Assign):
        w = sig_w.get(stmt.target, 32)
        val = _expr(stmt.value, src, f)
        lines.append(f'{pad}{dst}.{f(stmt.target)} = wmask({val}, {w}u);')

    elif isinstance(stmt, If):
        cond = _expr(stmt.cond, src, f)
        lines.append(f'{pad}if ({cond} != 0u) {{')
        for s in stmt.then_body:
            _emit_stmt(s, lines, sig_w, dst, src, f, indent + 1)
        if stmt.else_body:
            if len(stmt.else_body) == 1 and isinstance(stmt.else_body[0], If):
                lines.append(f'{pad}}} else')
                _emit_stmt(stmt.else_body[0], lines, sig_w, dst, src, f, indent)
            else:
                lines.append(f'{pad}}} else {{')
                for s in stmt.else_body:
                    _emit_stmt(s, lines, sig_w, dst, src, f, indent + 1)
                lines.append(f'{pad}}}')
        else:
            lines.append(f'{pad}}}')

    elif isinstance(stmt, Case):
        sel = _expr(stmt.sel, src, f)
        lines.append(f'{pad}switch ({sel}) {{')
        for val, body in stmt.cases:
            v = _expr(val, src, f)
            lines.append(f'{pad}    case {v}: {{')
            for s in body:
                _emit_stmt(s, lines, sig_w, dst, src, f, indent + 2)
            lines.append(f'{pad}    }}')
        if stmt.default:
            lines.append(f'{pad}    default: {{')
            for s in stmt.default:
                _emit_stmt(s, lines, sig_w, dst, src, f, indent + 2)
            lines.append(f'{pad}    }}')
        else:
            lines.append(f'{pad}    default: {{}}')
        lines.append(f'{pad}}}')

    elif isinstance(stmt, SliceAssign):
        w = sig_w.get(stmt.target, 32)
        val = _expr(stmt.value, src, f)
        tgt = f'{dst}.{f(stmt.target)}'
        if isinstance(stmt.hi, Const) and isinstance(stmt.lo, Const):
            lo, hi = stmt.lo.value, stmt.hi.value
            sw = hi - lo + 1
            m = ((1 << sw) - 1) << lo
            lines.append(f'{pad}{tgt} = wmask(({tgt} & {(~m) & 0xFFFFFFFF}u) | (({val} & {(1 << sw) - 1}u) << {lo}u), {w}u);')
        else:
            lo = _expr(stmt.lo, src, f)
            hi = _expr(stmt.hi, src, f)
            lines.append(f'{pad}{{ let _lo = {lo}; let _hi = {hi}; let _w = _hi - _lo + 1u;')
            lines.append(f'{pad}  let _m = ((1u << _w) - 1u) << _lo;')
            lines.append(f'{pad}  {tgt} = wmask(({tgt} & ~_m) | (({val} & ((1u << _w) - 1u)) << _lo), {w}u);')
            lines.append(f'{pad}}}')
