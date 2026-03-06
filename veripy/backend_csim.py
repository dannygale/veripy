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
import time

from .ir import (
    Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock, IRModule,
    Port, WireDecl, RegDecl, MemDecl,
    Delay, Display, Finish, ForLoop, Repeat, Disable,
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
    # Mark memory arrays so Index can distinguish mem[addr] from reg[bit]
    for m in ir.mems:
        w[f'__mem_{m.name}'] = True
    return w


def _eval_order_sigs(ir: IRModule) -> list[str]:
    """Return signal names ordered by first access in evaluation order.

    Walks comb_blocks (already topo-sorted) then seq_blocks, collecting
    writes then reads for each block.  Signals accessed by the same block
    end up adjacent in the returned list, improving spatial locality in
    the State struct.
    """
    from .flatten import _expr_reads, _stmt_writes_reads
    seen = set()
    order = []

    def _add(name):
        if name not in seen:
            seen.add(name)
            order.append(name)

    # Continuous assigns
    for a in ir.assigns:
        for s in sorted(_expr_reads(a.value)):
            _add(s)
        _add(a.target)

    # Comb blocks in topo order
    for blk in ir.comb_blocks:
        w, r = set(), set()
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, w, r)
        for s in sorted(r):
            _add(s)
        for s in sorted(w):
            _add(s)

    # Seq blocks
    for blk in ir.seq_blocks:
        w, r = set(), set()
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, w, r)
        for s in sorted(r):
            _add(s)
        for s in sorted(w):
            _add(s)

    return order


def _collect_nba_signals(ir: IRModule) -> tuple[set, list[set]]:
    """Return (global_nba_set, per_seq_block_write_sets).

    Only state signals (ports/wires/regs) need NBA temporaries.
    Block-local variables are excluded.
    """
    from .flatten import _stmt_writes_reads
    state_sigs = (
        {p.name for p in ir.ports}
        | {d.name for d in ir.wires}
        | {d.name for d in ir.regs}
    )
    per_block: list[set] = []
    nba: set = set()
    for blk in ir.seq_blocks:
        w: set = set()
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, w, set())
        blk_nba = w & state_sigs
        per_block.append(blk_nba)
        nba |= blk_nba
    return nba, per_block


def _build_comb_deps(ir: IRModule) -> list[set]:
    """Return list of read-signal sets, one per comb block (task #64).

    Each set contains the names of signals read by that comb block.
    Used to determine which blocks need re-evaluation after seq commits.
    """
    from .flatten import _stmt_writes_reads
    deps = []
    for blk in ir.comb_blocks:
        reads: set = set()
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, set(), reads)
        deps.append(reads)
    return deps


# ── Expression emitter ───────────────────────────────────────────────

def _pack_read(name, pack_map):
    """C expression to read a 1-bit packed signal."""
    word, bit = pack_map[name]
    return f'((s->{word} >> {bit}ULL) & 1ULL)'


def _pack_write(name, val_expr, pack_map):
    """C statement to write a 1-bit packed signal."""
    word, bit = pack_map[name]
    return (f's->{word} = (s->{word} & ~(1ULL << {bit}ULL)) '
            f'| ((({val_expr}) & 1ULL) << {bit}ULL);')


def _build_pack_map(all_sigs, ordered_names):
    """Build packing map for 1-bit signals.

    Returns (pack_map, pack_words) where pack_map maps
    signal_name → (word_name, bit_offset) and pack_words is the
    list of uint64_t word names needed in the struct.
    """
    one_bit = [n for n in ordered_names if all_sigs.get(n) == 1]
    pack_map = {}
    pack_words = []
    for i, name in enumerate(one_bit):
        word_idx, bit = divmod(i, 64)
        word_name = f'_pack_{word_idx}'
        if bit == 0:
            pack_words.append(word_name)
        pack_map[name] = (word_name, bit)
    return pack_map, pack_words


def _expr(node, sig_w, pack_map=None) -> str:
    """Emit a C expression string from an IR Expr node."""
    if isinstance(node, Const):
        v = node.value
        if v < 0:
            return f'((uint64_t)({v}))'
        return f'{v}ULL'
    if isinstance(node, Param):
        return str(node.name)
    if isinstance(node, Sig):
        if pack_map and node.name in pack_map:
            return _pack_read(node.name, pack_map)
        return f's->{node.name}'
    if isinstance(node, BinOp):
        l, r = _expr(node.left, sig_w, pack_map), _expr(node.right, sig_w, pack_map)
        return f'({l} {node.op} {r})'
    if isinstance(node, UnaryOp):
        op = node.op
        if op == '!':
            return f'(!{_expr(node.operand, sig_w, pack_map)})'
        return f'({op}{_expr(node.operand, sig_w, pack_map)})'
    if isinstance(node, Compare):
        l, r = _expr(node.left, sig_w, pack_map), _expr(node.right, sig_w, pack_map)
        return f'({l} {node.op} {r})'
    if isinstance(node, BoolOp):
        parts = []
        for v in node.values:
            s = _expr(v, sig_w, pack_map)
            if isinstance(v, BoolOp):
                s = f'({s})'
            parts.append(s)
        return f' {node.op} '.join(parts)
    if isinstance(node, Mux):
        s, t, f = (_expr(node.sel, sig_w, pack_map), _expr(node.true_val, sig_w, pack_map),
                   _expr(node.false_val, sig_w, pack_map))
        return f'({s} ? {t} : {f})'
    if isinstance(node, Slice):
        sig = _expr(node.signal, sig_w, pack_map)
        lo = _expr(node.lo, sig_w, pack_map)
        if node.hi is None:
            return f'(({sig} >> {lo}) & 1ULL)'
        hi = _expr(node.hi, sig_w, pack_map)
        return f'(({sig} >> {lo}) & ((1ULL << ({hi} - {lo} + 1ULL)) - 1ULL))'
    if isinstance(node, Index):
        name = node.signal.name if isinstance(node.signal, Sig) else None
        idx = _expr(node.idx, sig_w, pack_map)
        # Check if this is a memory (array) or a bit index on a register
        if name and sig_w.get(f'__mem_{name}'):
            return f's->{name}[{idx}]'
        # Bit index on a register/wire
        sig = _expr(node.signal, sig_w, pack_map)
        return f'(({sig} >> {idx}) & 1ULL)'
    if isinstance(node, Concat):
        # MSB-first: parts[0] is MSB
        parts = node.parts
        if not parts:
            return '0ULL'
        result = _expr(parts[-1], sig_w, pack_map)
        shift = _expr_width(parts[-1], sig_w)
        for p in reversed(parts[:-1]):
            pw = _expr_width(p, sig_w)
            result = f'(({_expr(p, sig_w, pack_map)} << {shift}ULL) | {result})'
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

def _emit_stmt(stmt, lines, sig_w, indent=1, pack_map=None, nba_sigs=None):
    """Emit C statements from an IR Stmt node.

    When *nba_sigs* is provided (seq block context), writes to those signals
    are redirected to ``s->_nba_<name>`` temporaries so that NBA semantics
    are preserved across concurrent seq blocks.
    """
    pad = '    ' * indent

    if isinstance(stmt, Assign):
        w = sig_w.get(stmt.target, 0)
        val = _expr(stmt.value, sig_w, pack_map)
        if nba_sigs and stmt.target in nba_sigs:
            # NBA: write to temporary; type matches signal width
            if w and w < 64:
                lines.append(f'{pad}s->_nba_{stmt.target} = ({_ctype(w)})({val} & {_mask(w)});')
            else:
                lines.append(f'{pad}s->_nba_{stmt.target} = {val};')
        elif pack_map and stmt.target in pack_map:
            lines.append(f'{pad}{_pack_write(stmt.target, val, pack_map)}')
        elif w and w < 64:
            lines.append(f'{pad}s->{stmt.target} = ({_ctype(w)})({val} & {_mask(w)});')
        else:
            lines.append(f'{pad}s->{stmt.target} = {val};')

    elif isinstance(stmt, SliceAssign):
        lo = _expr(stmt.lo, sig_w, pack_map)
        hi = _expr(stmt.hi, sig_w, pack_map)
        val = _expr(stmt.value, sig_w, pack_map)
        # Clear bits [hi:lo], then set them
        tgt = f's->_nba_{stmt.target}' if (nba_sigs and stmt.target in nba_sigs) else f's->{stmt.target}'
        lines.append(f'{pad}{{')
        lines.append(f'{pad}    uint64_t _lo = {lo};')
        lines.append(f'{pad}    uint64_t _hi = {hi};')
        lines.append(f'{pad}    uint64_t _w = _hi - _lo + 1;')
        lines.append(f'{pad}    uint64_t _mask = ((1ULL << _w) - 1) << _lo;')
        lines.append(f'{pad}    {tgt} = ({tgt} & ~_mask) | ((({val}) << _lo) & _mask);')
        lines.append(f'{pad}}}')

    elif isinstance(stmt, MemWrite):
        val = _expr(stmt.data, sig_w, pack_map)
        addr = _expr(stmt.addr, sig_w, pack_map)
        lines.append(f'{pad}s->{stmt.mem}[{addr}] = {val};')

    elif isinstance(stmt, If):
        lines.append(f'{pad}if ({_expr(stmt.cond, sig_w, pack_map)}) {{')
        for s in stmt.then_body:
            _emit_stmt(s, lines, sig_w, indent + 1, pack_map, nba_sigs)
        if stmt.else_body:
            if len(stmt.else_body) == 1 and isinstance(stmt.else_body[0], If):
                lines.append(f'{pad}}} else')
                _emit_stmt(stmt.else_body[0], lines, sig_w, indent, pack_map, nba_sigs)
            else:
                lines.append(f'{pad}}} else {{')
                for s in stmt.else_body:
                    _emit_stmt(s, lines, sig_w, indent + 1, pack_map, nba_sigs)
                lines.append(f'{pad}}}')
        else:
            lines.append(f'{pad}}}')

    elif isinstance(stmt, Case):
        lines.append(f'{pad}switch ({_expr(stmt.sel, sig_w, pack_map)}) {{')
        for val, body in stmt.cases:
            lines.append(f'{pad}    case {_expr(val, sig_w, pack_map)}:')
            for s in body:
                _emit_stmt(s, lines, sig_w, indent + 2, pack_map, nba_sigs)
            lines.append(f'{pad}        break;')
        if stmt.default:
            lines.append(f'{pad}    default:')
            for s in stmt.default:
                _emit_stmt(s, lines, sig_w, indent + 2, pack_map, nba_sigs)
            lines.append(f'{pad}        break;')
        lines.append(f'{pad}}}')



# ── Inline hint helpers ──────────────────────────────────────────────

_INLINE_THRESHOLD = 10  # max statements for always_inline


def _count_stmts(stmts) -> int:
    """Recursively count statements in a block."""
    n = 0
    for s in stmts:
        n += 1
        if isinstance(s, If):
            n += _count_stmts(s.then_body) + _count_stmts(s.else_body)
        elif isinstance(s, Case):
            for _, body in s.cases:
                n += _count_stmts(body)
            if s.default:
                n += _count_stmts(s.default)
    return n


def _inline_attr(stmt_count: int) -> str:
    """Return always_inline attribute for small blocks, empty string otherwise."""
    if stmt_count <= _INLINE_THRESHOLD:
        return '__attribute__((always_inline)) '
    return ''


def _find_merge_groups(ir: IRModule) -> list:
    """Group adjacent comb blocks that can be merged into a single function.

    Two adjacent blocks i and i+1 can be merged when every signal written
    by block i is:
      - not an output port (must remain visible after eval),
      - not read by any seq block (seq blocks run after comb settle), and
      - only read by block i+1 (no other comb block consumes it).

    Returns a list of groups; each group is a list of consecutive block
    indices.  Single-element groups are not merged but may be inlined.
    """
    from .flatten import _stmt_writes_reads

    n = len(ir.comb_blocks)
    if n == 0:
        return []

    block_writes, block_reads = [], []
    for blk in ir.comb_blocks:
        w: set = set()
        r: set = set()
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, w, r)
        block_writes.append(w)
        block_reads.append(r)

    seq_reads: set = set()
    for blk in ir.seq_blocks:
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, set(), seq_reads)

    output_ports = {p.name for p in ir.ports if p.direction == 'output'}

    sig_readers: dict = {}
    for i, reads in enumerate(block_reads):
        for sig in reads:
            sig_readers.setdefault(sig, set()).add(i)

    groups: list = []
    current = [0]
    for i in range(n - 1):
        mergeable = all(
            sig not in output_ports
            and sig not in seq_reads
            and sig_readers.get(sig, set()) == {i + 1}
            for sig in block_writes[i]
        )
        if mergeable:
            current.append(i + 1)
        else:
            groups.append(current)
            current = [i + 1]
    groups.append(current)
    return groups


# ── Clock alias resolution ───────────────────────────────────────────


def _resolve_clock_aliases(ir: IRModule) -> dict:
    """Return map from aliased clock name → physical clock name.

    Traces chains of continuous assigns where the RHS is a bare signal
    (``assign a = b``).  Stops when no further alias exists.
    """
    direct = {a.target: a.value.name for a in ir.assigns if isinstance(a.value, Sig)}
    alias_map = {}
    for blk in ir.seq_blocks:
        for _, sig in blk.edges:
            if sig in alias_map:
                continue
            src, visited = sig, set()
            while src in direct and src not in visited:
                visited.add(src)
                src = direct[src]
            alias_map[sig] = src
    return alias_map


# ── Cont-assign inlining ─────────────────────────────────────────────


def _inline_cont_assigns(ir: IRModule) -> IRModule:
    """Inline trivial cont-assign wires into dependent comb blocks.

    After topo_sort_comb, cont assigns become single-statement CombBlocks
    writing to wires.  If a wire is written by exactly one such block and
    is not read by any seq block, substitute its expression at every read
    site and remove the intermediate wire + writing block.
    """
    from copy import deepcopy
    from .flatten import _stmt_writes_reads

    wire_names = {w.name for w in ir.wires}

    # Collect candidates: wire written by exactly one single-stmt CombBlock
    write_count: dict = {}
    candidates: dict = {}
    for blk in ir.comb_blocks:
        if len(blk.stmts) == 1 and isinstance(blk.stmts[0], Assign):
            name = blk.stmts[0].target
            write_count[name] = write_count.get(name, 0) + 1
            if name in wire_names:
                candidates[name] = blk.stmts[0].value

    candidates = {n: e for n, e in candidates.items() if write_count.get(n, 0) == 1}

    # Drop candidates read by seq blocks
    seq_reads: set = set()
    for blk in ir.seq_blocks:
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, set(), seq_reads)
    candidates = {n: e for n, e in candidates.items() if n not in seq_reads}

    if not candidates:
        return ir

    def _se(expr):
        """Recursively substitute inlineable signals in an expression."""
        if isinstance(expr, Sig):
            if expr.name in candidates:
                return _se(candidates[expr.name])
            return expr
        if isinstance(expr, BinOp):
            return BinOp(expr.op, _se(expr.left), _se(expr.right))
        if isinstance(expr, UnaryOp):
            return UnaryOp(expr.op, _se(expr.operand))
        if isinstance(expr, Compare):
            return Compare(expr.op, _se(expr.left), _se(expr.right))
        if isinstance(expr, BoolOp):
            return BoolOp(expr.op, [_se(v) for v in expr.values])
        if isinstance(expr, Mux):
            return Mux(_se(expr.sel), _se(expr.true_val), _se(expr.false_val))
        if isinstance(expr, Slice):
            return Slice(_se(expr.signal),
                         _se(expr.hi) if expr.hi is not None else None,
                         _se(expr.lo))
        if isinstance(expr, Index):
            return Index(_se(expr.signal), _se(expr.idx))
        if isinstance(expr, Concat):
            return Concat([_se(p) for p in expr.parts])
        return expr

    def _ss(stmts):
        """Substitute inlineable signals in a list of statements."""
        out = []
        for stmt in stmts:
            if isinstance(stmt, Assign):
                out.append(Assign(stmt.target, _se(stmt.value), stmt.blocking))
            elif isinstance(stmt, SliceAssign):
                out.append(SliceAssign(stmt.target,
                    _se(stmt.hi) if stmt.hi is not None else None,
                    _se(stmt.lo), _se(stmt.value), stmt.blocking))
            elif isinstance(stmt, If):
                out.append(If(_se(stmt.cond), _ss(stmt.then_body), _ss(stmt.else_body)))
            elif isinstance(stmt, Case):
                out.append(Case(_se(stmt.sel),
                    [(v, _ss(b)) for v, b in stmt.cases],
                    _ss(stmt.default) if stmt.default else []))
            elif isinstance(stmt, MemWrite):
                out.append(MemWrite(stmt.mem, _se(stmt.addr), _se(stmt.data), stmt.blocking))
            else:
                out.append(stmt)
        return out

    ir = deepcopy(ir)
    ir.wires = [w for w in ir.wires if w.name not in candidates]
    new_comb = []
    for blk in ir.comb_blocks:
        if (len(blk.stmts) == 1 and isinstance(blk.stmts[0], Assign)
                and blk.stmts[0].target in candidates):
            continue
        new_comb.append(CombBlock(_ss(blk.stmts), blk.locals))
    ir.comb_blocks = new_comb
    return ir


# ── Dirty-flag helpers ───────────────────────────────────────────────


def _build_dirty_indices(sig_w: dict) -> tuple:
    """Assign a dirty bit index to every signal in sig_w.

    Returns (dirty_idx, n_words) where dirty_idx maps
    signal_name → (word_idx, bit_mask) and n_words is the number of
    uint64_t words needed to hold all bits.
    """
    dirty_idx: dict = {}
    idx = 0
    for name in sorted(sig_w.keys()):
        if not name.startswith('__'):   # skip internal markers like __mem_X
            dirty_idx[name] = (idx // 64, 1 << (idx % 64))
            idx += 1
    return dirty_idx, max(1, (idx + 63) // 64)


def _dirty_cond(sigs, dirty_idx) -> str:
    """Return a C condition that is true when any signal in *sigs* is dirty."""
    masks: dict = {}
    for sig in sigs:
        if sig in dirty_idx:
            w, m = dirty_idx[sig]
            masks[w] = masks.get(w, 0) | m
    if not masks:
        return '1'
    return ' || '.join(f'(s->_dirty[{w}] & {m}ULL)' for w, m in sorted(masks.items()))


def _dirty_set_lines(sigs, dirty_idx, indent: int = 1) -> list:
    """Return C statements that set dirty bits for all signals in *sigs*."""
    masks: dict = {}
    for sig in sigs:
        if sig in dirty_idx:
            w, m = dirty_idx[sig]
            masks[w] = masks.get(w, 0) | m
    pad = '    ' * indent
    return [f'{pad}s->_dirty[{w}] |= {m}ULL;' for w, m in sorted(masks.items())]


# ── Top-level C emitter ──────────────────────────────────────────────


def emit_c(ir: IRModule) -> str:
    """Emit C source from a flat, topo-sorted IRModule.

    The module should have been processed through ``flatten_ir`` and
    ``topo_sort_comb`` before calling this function.

    Each comb_block and seq_block is emitted as its own ``static`` C
    function.  Small blocks (≤ ``_INLINE_THRESHOLD`` statements) are
    annotated with ``always_inline``.  ``veripy_eval()`` calls them in
    topological order.

    Returns:
        Complete C source string.
    """
    sig_w = _build_sig_widths(ir)
    nba_sigs, nba_per_seq = _collect_nba_signals(ir)
    comb_deps = _build_comb_deps(ir)
    merge_groups = _find_merge_groups(ir)
    clock_aliases = _resolve_clock_aliases(ir)
    dirty_idx, n_dirty_words = _build_dirty_indices(sig_w)

    # Compute write sets for each comb block (for dirty output marking)
    from .flatten import _stmt_writes_reads as _swr
    comb_writes: list = []
    for blk in ir.comb_blocks:
        ws: set = set()
        for stmt in blk.stmts:
            _swr(stmt, ws, set())
        comb_writes.append(ws)

    # Compute write sets for each seq block (for dirty output marking)
    seq_writes: list = []
    for blk in ir.seq_blocks:
        ws = set()
        for stmt in blk.stmts:
            _swr(stmt, ws, set())
        seq_writes.append(ws & set(dirty_idx))

    # Which groups contain at least one block that reads a seq-written signal
    resettl_groups = [
        g for g in merge_groups
        if any(comb_deps[i] & nba_sigs for i in g)
    ]
    lines = [
        '#include <stdint.h>',
        '#include <stdlib.h>',
        '#include <string.h>',
        '',
    ]

    # ── State struct ─────────────────────────────────────────────
    lines.append('typedef struct {')

    # Build name→width for all signals
    all_sigs = {}
    for p in ir.ports:
        all_sigs.setdefault(p.name, _resolve_width(p.width, ir.params))
    for d in ir.wires:
        all_sigs.setdefault(d.name, _resolve_width(d.width, ir.params))
    for d in ir.regs:
        all_sigs.setdefault(d.name, _resolve_width(d.width, ir.params))
    for blk in ir.comb_blocks:
        for name, width in blk.locals.items():
            all_sigs.setdefault(name, _resolve_width(width, ir.params))
    for blk in ir.seq_blocks:
        for name, width in blk.locals.items():
            all_sigs.setdefault(name, _resolve_width(width, ir.params))

    # Order fields by evaluation access pattern for spatial locality
    eval_order = _eval_order_sigs(ir)
    eval_rank = {name: i for i, name in enumerate(eval_order)}
    ordered_names = sorted(all_sigs, key=lambda n: eval_rank.get(n, len(eval_order)))

    # Pack 1-bit signals into uint64_t bitfield words
    pack_map, pack_words = _build_pack_map(all_sigs, ordered_names)

    for name in ordered_names:
        if name not in pack_map:
            lines.append(f'    {_ctype(all_sigs[name])} {name};')
    for pw in pack_words:
        lines.append(f'    uint64_t {pw};')

    # Memory arrays
    for m in ir.mems:
        if isinstance(m, MemDecl):
            w = _resolve_width(m.width, ir.params)
            lines.append(f'    {_ctype(w)} {m.name}[{m.depth}];')

    # Previous values for edge detection (use physical clock names)
    clocks = set()
    for blk in ir.seq_blocks:
        for edge_kind, sig_name in blk.edges:
            clocks.add(clock_aliases.get(sig_name, sig_name))
    for clk in sorted(clocks):
        lines.append(f'    uint8_t _prev_{clk};')

    # NBA temporaries for signals written in seq blocks
    for name in sorted(nba_sigs):
        w = all_sigs.get(name, 32)
        lines.append(f'    {_ctype(w)} _nba_{name};')

    # Dirty bits: one bit per signal, packed into uint64_t words
    lines.append(f'    uint64_t _dirty[{n_dirty_words}];')

    lines.append('} State;')
    lines.append('')

    # ── create / destroy ─────────────────────────────────────────
    lines.append('void* veripy_create(void) {')
    lines.append('    State* s = calloc(1, sizeof(State));')
    lines.append('    memset(s->_dirty, 0xFF, sizeof(s->_dirty));  /* first eval runs all */')
    lines.append('    return s;')
    lines.append('}')
    lines.append('')
    lines.append('void veripy_destroy(void* p) {')
    lines.append('    free(p);')
    lines.append('}')
    lines.append('')

    # ── Per-block static functions ───────────────────────────────

    # Continuous assigns → _cont_assigns()
    has_cont = bool(ir.assigns)
    if has_cont:
        cont_stmts = []
        for a in ir.assigns:
            w = sig_w.get(a.target, 0)
            val = _expr(a.value, sig_w, pack_map)
            if pack_map and a.target in pack_map:
                cont_stmts.append(f'    {_pack_write(a.target, val, pack_map)}')
            elif w and w < 64:
                cont_stmts.append(f'    s->{a.target} = ({_ctype(w)})({val} & {_mask(w)});')
            else:
                cont_stmts.append(f'    s->{a.target} = {val};')
        attr = _inline_attr(len(ir.assigns))
        lines.append(f'static {attr}void _cont_assigns(State* s) {{')
        lines.extend(cont_stmts)
        lines.append('}')
        lines.append('')

    # Each comb group → _comb_N() (merged), or inlined if trivial (1 stmt, 1 block)
    for gi, group in enumerate(merge_groups):
        all_stmts = []
        for idx in group:
            all_stmts.extend(ir.comb_blocks[idx].stmts)
        total = _count_stmts(all_stmts)
        if len(group) == 1 and total == 1:
            continue  # will be inlined into veripy_eval()
        body = []
        for stmt in all_stmts:
            _emit_stmt(stmt, body, sig_w, pack_map=pack_map)
        attr = _inline_attr(total)
        lines.append(f'static {attr}void _comb_{gi}(State* s) {{')
        lines.extend(body)
        lines.append('}')
        lines.append('')

    # Each seq_block → _seq_N()
    for i, blk in enumerate(ir.seq_blocks):
        body = []
        for stmt in blk.stmts:
            _emit_stmt(stmt, body, sig_w, pack_map=pack_map, nba_sigs=nba_sigs)
        attr = _inline_attr(_count_stmts(blk.stmts))
        lines.append(f'static {attr}void _seq_{i}(State* s) {{')
        lines.extend(body)
        lines.append('}')
        lines.append('')

    # ── eval ─────────────────────────────────────────────────────
    lines.append('void veripy_eval(void* p) {')
    lines.append('    State* s = (State*)p;')

    def _emit_comb_group(gi, group, mark_outputs):
        """Emit dirty-guarded comb group dispatch."""
        all_stmts = []
        for idx in group:
            all_stmts.extend(ir.comb_blocks[idx].stmts)
        # Compute group read/write sets
        group_reads: set = set()
        group_writes: set = set()
        for idx in group:
            group_reads |= comb_deps[idx]
            group_writes |= comb_writes[idx]
        cond = _dirty_cond(group_reads, dirty_idx)
        out_lines = _dirty_set_lines(group_writes, dirty_idx, indent=2) if mark_outputs else []
        trivial = len(group) == 1 and _count_stmts(all_stmts) == 1
        if cond == '1':
            if trivial:
                body = []
                _emit_stmt(all_stmts[0], body, sig_w, pack_map=pack_map)
                lines.extend('    ' + ln.lstrip() for ln in body)
            else:
                lines.append(f'    _comb_{gi}(s);')
            lines.extend(out_lines)
        else:
            lines.append(f'    if ({cond}) {{')
            if trivial:
                body = []
                _emit_stmt(all_stmts[0], body, sig_w, pack_map=pack_map)
                lines.extend('        ' + ln.lstrip() for ln in body)
            else:
                lines.append(f'        _comb_{gi}(s);')
            lines.extend(out_lines)
            lines.append('    }')

    # 1. Settle combinational logic (dirty-driven; mark outputs dirty for propagation)
    if has_cont:
        lines.append('    _cont_assigns(s);')
    for gi, group in enumerate(merge_groups):
        _emit_comb_group(gi, group, mark_outputs=True)

    # Clear dirty bits after initial comb settle
    lines.append(f'    memset(s->_dirty, 0, {n_dirty_words * 8});')

    # 2. Edge detection + sequential block calls (grouped by physical clock)
    edge_blocks = {}
    for i, blk in enumerate(ir.seq_blocks):
        for edge_kind, sig_name in blk.edges:
            phys = clock_aliases.get(sig_name, sig_name)
            edge_blocks.setdefault((edge_kind, phys), []).append(i)

    for (edge_kind, clk), block_ids in sorted(edge_blocks.items()):
        clk_expr = _pack_read(clk, pack_map) if clk in pack_map else f's->{clk}'
        if edge_kind == 'posedge':
            cond = f'{clk_expr} && !s->_prev_{clk}'
        else:
            cond = f'!{clk_expr} && s->_prev_{clk}'
        lines.append(f'    if ({cond}) {{')
        # Only snapshot/commit signals written by seq blocks in this edge group
        group_nba = set()
        for idx in block_ids:
            group_nba |= nba_per_seq[idx]
        for name in sorted(group_nba):
            src = _pack_read(name, pack_map) if name in pack_map else f's->{name}'
            lines.append(f'        s->_nba_{name} = {src};')
        for idx in block_ids:
            lines.append(f'        _seq_{idx}(s);')
        for name in sorted(group_nba):
            if name in pack_map:
                lines.append(f'        {_pack_write(name, f"s->_nba_{name}", pack_map)}')
            else:
                lines.append(f'        s->{name} = s->_nba_{name};')
        # Mark seq outputs dirty so re-settle comb blocks run
        group_seq_writes: set = set()
        for idx in block_ids:
            group_seq_writes |= seq_writes[idx]
        lines.extend(_dirty_set_lines(group_seq_writes, dirty_idx, indent=2))
        lines.append('    }')

    # 3. Re-settle combinational logic (dirty-driven; no output marking needed)
    if has_cont:
        lines.append('    _cont_assigns(s);')
    for group in resettl_groups:
        gi = merge_groups.index(group)
        _emit_comb_group(gi, group, mark_outputs=False)

    # 4. Update previous values
    for clk in sorted(clocks):
        clk_expr = _pack_read(clk, pack_map) if clk in pack_map else f's->{clk}'
        lines.append(f'    s->_prev_{clk} = {clk_expr};')

    # Clear dirty bits at end of eval
    lines.append(f'    memset(s->_dirty, 0, {n_dirty_words * 8});')

    lines.append('}')
    lines.append('')

    # ── Per-signal set/get ───────────────────────────────────────
    for p in ir.ports:
        w = _resolve_width(p.width, ir.params)
        if p.name in pack_map:
            word, bit = pack_map[p.name]
            if p.direction == 'input':
                dirty_stmt = ''
                if p.name in dirty_idx:
                    dw, dm = dirty_idx[p.name]
                    dirty_stmt = f' s->_dirty[{dw}] |= {dm}ULL;'
                lines.append(
                    f'void veripy_set_{p.name}(void* p, uint64_t v) '
                    f'{{ State* s = (State*)p; '
                    f's->{word} = (s->{word} & ~(1ULL << {bit}ULL)) '
                    f'| ((v & 1ULL) << {bit}ULL);{dirty_stmt} }}')
            lines.append(
                f'uint64_t veripy_get_{p.name}(void* p) '
                f'{{ return (((State*)p)->{word} >> {bit}ULL) & 1ULL; }}')
        else:
            if p.direction == 'input':
                dirty_stmt = ''
                if p.name in dirty_idx:
                    dw, dm = dirty_idx[p.name]
                    dirty_stmt = f' s->_dirty[{dw}] |= {dm}ULL;'
                lines.append(f'void veripy_set_{p.name}(void* p, uint64_t v) '
                             f'{{ State* s = (State*)p; '
                             f's->{p.name} = ({_ctype(w)})(v & {_mask(w)});{dirty_stmt} }}')
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
        ir = _inline_cont_assigns(ir)

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
            [cc, '-O3', '-march=native', '-flto', '-fPIC', flag, '-o', lib_path, c_path],
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


# ── Native C testbench ───────────────────────────────────────────────

def _extract_half_period(always_stmts):
    """Extract the half-period T from an always block like: clk=0; delay T; clk=1; delay T."""
    for s in always_stmts:
        if isinstance(s, Delay):
            if isinstance(s.value, Const):
                return s.value.value
            if isinstance(s.value, BinOp) and s.value.op == '*':
                l = s.value.left.value if isinstance(s.value.left, Const) else None
                r = s.value.right.value if isinstance(s.value.right, Const) else None
                if l is not None and r is not None:
                    return l * r
    return 10  # fallback


def _extract_clock_name(always_stmts):
    """Extract clock signal name from always block assigns."""
    for s in always_stmts:
        if isinstance(s, Assign):
            return s.target
    return 'clock'


def emit_tb_c(tb_ir, model_c_src, half_period=10, model_ir=None):
    """Emit a self-contained C file: model + testbench run_bench() entry point.

    The always block is folded into a step() helper. The initial block
    becomes straight-line C inside run_bench().  Since the TB is compiled
    into the same .so as the model, it uses direct State struct access
    (task #66, #72) instead of veripy_set/get API calls.
    """
    # Build set of model signal names from IR ports
    model_sigs = set()
    input_sigs = set()
    if model_ir:
        for p in model_ir.ports:
            model_sigs.add(p.name)
            if p.direction == 'input':
                input_sigs.add(p.name)
    else:
        # Fallback: scan C source for set/get functions
        for line in model_c_src.split('\n'):
            if 'veripy_set_' in line:
                name = line.split('veripy_set_')[1].split('(')[0]
                model_sigs.add(name)
                input_sigs.add(name)
            elif 'veripy_get_' in line and 'veripy_set_' not in line:
                name = line.split('veripy_get_')[1].split('(')[0]
                model_sigs.add(name)

    # Build pack_map for direct struct access (task #66, #72)
    tb_pack_map = {}
    if model_ir:
        _all_sigs = {}
        for p in model_ir.ports:
            _all_sigs.setdefault(p.name, _resolve_width(p.width, model_ir.params))
        for d in model_ir.wires:
            _all_sigs.setdefault(d.name, _resolve_width(d.width, model_ir.params))
        for d in model_ir.regs:
            _all_sigs.setdefault(d.name, _resolve_width(d.width, model_ir.params))
        _eval_order = _eval_order_sigs(model_ir)
        _eval_rank = {n: i for i, n in enumerate(_eval_order)}
        _ordered = sorted(_all_sigs, key=lambda n: _eval_rank.get(n, len(_eval_order)))
        tb_pack_map, _ = _build_pack_map(_all_sigs, _ordered)

    clock_name = _extract_clock_name(tb_ir.always_blocks[0].stmts) if tb_ir.always_blocks else 'clock'

    lines = [model_c_src.rstrip()]
    lines.append('')
    lines.append('/* ── Testbench ─────────────────────────────────── */')
    lines.append('')
    # task #66: direct struct clock toggle instead of veripy_set/get
    lines.append(f'static void _step(void* p, int time_units) {{')
    lines.append(f'    State* s = (State*)p;')
    lines.append(f'    int n = time_units / {half_period};')
    lines.append(f'    for (int _i = 0; _i < n; _i++) {{')
    if clock_name in tb_pack_map:
        word, bit = tb_pack_map[clock_name]
        lines.append(f'        s->{word} ^= (1ULL << {bit}ULL);')
    else:
        lines.append(f'        s->{clock_name} ^= 1;')
    lines.append(f'        veripy_eval(p);')
    lines.append(f'    }}')
    lines.append(f'}}')
    lines.append('')
    lines.append('uint64_t run_bench(void) {')
    lines.append('    void* p = veripy_create();')
    lines.append('    State* s = (State*)p;')

    # Collect local variables from initial blocks (ForLoop vars, non-model assigns)
    locals_declared = set()

    def _collect_locals(stmts):
        for s in stmts:
            if isinstance(s, ForLoop):
                locals_declared.add(s.var)
                _collect_locals(s.body)
            elif isinstance(s, (Repeat, If)):
                body = s.body if hasattr(s, 'body') else s.then_body
                _collect_locals(body)
                if hasattr(s, 'else_body') and s.else_body:
                    _collect_locals(s.else_body)
            elif isinstance(s, Assign) and s.target not in model_sigs:
                locals_declared.add(s.target)

    for blk in tb_ir.initial_blocks:
        _collect_locals(blk.stmts)

    for v in sorted(locals_declared):
        lines.append(f'    uint64_t {v} = 0;')

    def _tb_expr(node):
        if isinstance(node, Const):
            v = node.value
            return f'((uint64_t)({v}))' if v < 0 else f'{v}ULL'
        if isinstance(node, Sig):
            if node.name in locals_declared:
                return node.name
            # task #72: direct struct read instead of veripy_get_*
            if node.name in model_sigs:
                if node.name in tb_pack_map:
                    return _pack_read(node.name, tb_pack_map).replace('s->', 's->')
                return f's->{node.name}'
            return node.name
        if isinstance(node, BinOp):
            return f'({_tb_expr(node.left)} {node.op} {_tb_expr(node.right)})'
        if isinstance(node, UnaryOp):
            if node.op == '!':
                return f'(!{_tb_expr(node.operand)})'
            return f'({node.op}{_tb_expr(node.operand)})'
        if isinstance(node, Compare):
            return f'({_tb_expr(node.left)} {node.op} {_tb_expr(node.right)})'
        if isinstance(node, BoolOp):
            parts = [_tb_expr(v) for v in node.values]
            return f' {node.op} '.join(parts)
        if isinstance(node, Mux):
            return f'({_tb_expr(node.sel)} ? {_tb_expr(node.true_val)} : {_tb_expr(node.false_val)})'
        raise ValueError(f'TB emit: unsupported expr: {node}')

    def _tb_stmt(stmt, indent=1):
        pad = '    ' * indent
        if isinstance(stmt, Assign):
            val = _tb_expr(stmt.value)
            if stmt.target in locals_declared:
                lines.append(f'{pad}{stmt.target} = {val};')
            elif stmt.target in input_sigs:
                # task #66/#72: direct struct write instead of veripy_set_*
                if stmt.target in tb_pack_map:
                    lines.append(f'{pad}{_pack_write(stmt.target, val, tb_pack_map).replace("s->", "s->")}'
                                 .replace('s->', 's->'))
                else:
                    sig_width = None
                    if model_ir:
                        for p in model_ir.ports:
                            if p.name == stmt.target:
                                sig_width = _resolve_width(p.width, model_ir.params)
                                break
                    if sig_width and sig_width < 64:
                        lines.append(f'{pad}s->{stmt.target} = ({_ctype(sig_width)})({val} & {_mask(sig_width)});')
                    else:
                        lines.append(f'{pad}s->{stmt.target} = {val};')
            else:
                # output signal — shouldn't be assigned in TB, but handle gracefully
                if stmt.target in tb_pack_map:
                    lines.append(f'{pad}{_pack_write(stmt.target, val, tb_pack_map)}')
                else:
                    lines.append(f'{pad}s->{stmt.target} = {val};')
        elif isinstance(stmt, Delay):
            lines.append(f'{pad}_step(p, {_tb_expr(stmt.value)});')
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
        elif isinstance(stmt, Display):
            pass
        elif isinstance(stmt, Finish):
            pass

    for blk in tb_ir.initial_blocks:
        for s in blk.stmts:
            _tb_stmt(s)

    lines.append('    veripy_destroy(p);')
    lines.append('    return 0;')
    lines.append('}')
    return '\n'.join(lines) + '\n'


def compile_bench(module, tb_ir, module_name=None):
    """Compile model + native C testbench into .so, return callable.

    Returns (run_fn, compile_time, cleanup_fn) where run_fn() executes
    the full benchmark in C and cleanup_fn() removes temp files.
    """
    from .signal import Signal, Interface
    from .lower import lower_module
    from .flatten import flatten_ir, topo_sort_comb
    from .emit_verilog import _to_snake

    if module_name is None:
        module_name = type(module).__name__.lower()

    t0 = time.perf_counter()

    # Build flat model IR
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
    flat_ir = topo_sort_comb(flat_ir)
    flat_ir = _inline_cont_assigns(flat_ir)

    model_c = emit_c(flat_ir)

    # Extract half-period from always block
    hp = _extract_half_period(tb_ir.always_blocks[0].stmts) if tb_ir.always_blocks else 10
    combined_c = emit_tb_c(tb_ir, model_c, hp, model_ir=flat_ir)

    build_dir = tempfile.mkdtemp(prefix='veripy_bench_')
    c_path = os.path.join(build_dir, 'bench.c')
    with open(c_path, 'w') as f:
        f.write(combined_c)

    ext = '.dylib' if os.uname().sysname == 'Darwin' else '.so'
    lib_path = os.path.join(build_dir, f'libbench{ext}')

    cc = os.environ.get('CC', 'cc')
    flag = '-dynamiclib' if ext == '.dylib' else '-shared'
    r = subprocess.run([cc, '-O3', '-march=native', '-flto', '-fPIC', flag, '-o', lib_path, c_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'Bench compilation failed:\n{r.stderr}\n\nSource:\n{combined_c}')

    compile_t = time.perf_counter() - t0

    lib = ctypes.CDLL(lib_path)
    lib.run_bench.restype = ctypes.c_uint64

    def run():
        lib.run_bench()

    def cleanup():
        shutil.rmtree(build_dir, ignore_errors=True)

    return run, compile_t, cleanup
