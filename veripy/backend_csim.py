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

def _collect_submodule_registry(module):
    """Build a registry of sub-module IRs, keyed by unique type+params.

    Different parameterizations of the same module (e.g. cache with
    LINE_SIZE=1 vs LINE_SIZE=4) get separate registry entries and
    distinct ``mod_type`` keys in the parent IR so that ``flatten_ir``
    resolves each instance to the correct IR.

    Returns (registry, patch_fn) where patch_fn(ir) updates inst.mod_type
    in an IR to match the registry keys.
    """
    from .lower import lower_module
    from .emit_verilog import _to_snake
    from .module import Module as _Module

    registry = {}
    # Map (base_type, frozen_int_params) → registry key
    _key_cache: dict[tuple, str] = {}

    def _cache_key(base, params_dict):
        int_params = tuple(sorted((k, v) for k, v in params_dict.items()
                                  if isinstance(v, (int, float))))
        return (base, int_params)

    def _make_key(mod):
        base = _to_snake(type(mod).__name__)
        params = getattr(mod, '_params', {})
        ck = _cache_key(base, params)
        if ck in _key_cache:
            return _key_cache[ck]
        key = base
        if ck[1]:
            key = base + '__' + '_'.join(f'{k}{v}' for k, v in ck[1])
        _key_cache[ck] = key
        return key

    def _collect(mod, parent_params=None):
        # Resolve string param references against parent
        params = getattr(mod, '_params', {})
        resolved = {}
        for k, v in params.items():
            if isinstance(v, (int, float)):
                resolved[k] = v
            elif isinstance(v, str) and parent_params and v in parent_params:
                resolved[k] = parent_params[v]
        key = _to_snake(type(mod).__name__)
        ck = _cache_key(key, resolved)
        if ck in _key_cache:
            return
        if ck[1]:
            key = key + '__' + '_'.join(f'{k}{v}' for k, v in ck[1])
        _key_cache[ck] = key
        if key in registry:
            return
        factory = getattr(type(mod), '_veripy_factory', None)
        if factory:
            fresh = factory(**resolved) if resolved else factory()
        else:
            fresh = type(mod)(**resolved) if resolved else type(mod)()
        fresh_params = getattr(fresh, '_params', {})
        for _sn, sub in fresh._submodules().items():
            _collect(sub, parent_params=fresh_params)
        registry[key] = lower_module(fresh, key)

    top_params = getattr(module, '_params', {})
    for attr in dir(module):
        v = getattr(module, attr)
        if isinstance(v, _Module) and v is not module:
            _collect(v, parent_params=top_params)

    def _patch_inst_types(ir):
        """Update inst.mod_type in *ir* to match registry keys."""
        parent_params = ir.params
        for inst in ir.instances:
            # Resolve string param refs so the cache key matches
            resolved = {}
            for k, v in inst.params.items():
                if isinstance(v, (int, float)):
                    resolved[k] = v
                elif isinstance(v, str) and v in parent_params:
                    resolved[k] = parent_params[v]
            ck = _cache_key(inst.mod_type, resolved)
            new_key = _key_cache.get(ck)
            if new_key and new_key != inst.mod_type:
                inst.mod_type = new_key

    # Patch all registered IRs
    for ir in registry.values():
        _patch_inst_types(ir)

    return registry, _patch_inst_types


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
    """Resolve a width that may be a param name or expression to an int."""
    if isinstance(w, int):
        return w
    if w in params:
        return params[w]
    # Try evaluating as expression with params (e.g. 'NUM_LINES*LINE_SIZE')
    try:
        return int(eval(w, {"__builtins__": {}}, params))
    except Exception:
        return 1


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
        if node.name in _c_locals:
            return node.name
        return f's->{node.name}'
    if isinstance(node, BinOp):
        l, r = _expr(node.left, sig_w, pack_map), _expr(node.right, sig_w, pack_map)
        return f'({l} {node.op} {r})'
    if isinstance(node, UnaryOp):
        op = node.op
        if op == '!':
            return f'(!{_expr(node.operand, sig_w, pack_map)})'
        if op == '~':
            inner = _expr(node.operand, sig_w, pack_map)
            # In C, ~(uint8_t)1 == 0xFE (truthy), but Verilog ~1'b1 == 1'b0.
            # Mask result for 1-bit signals to preserve Verilog semantics.
            if isinstance(node.operand, Sig) and sig_w.get(node.operand.name, 32) == 1:
                return f'((~{inner}) & 0x1ULL)'
            return f'(~{inner})'
        return f'({op}{_expr(node.operand, sig_w, pack_map)})'
    if isinstance(node, Compare):
        l, r = _expr(node.left, sig_w, pack_map), _expr(node.right, sig_w, pack_map)
        # Mask operands to their widths so C integer promotion doesn't
        # change overflow/wrap semantics (e.g. uint16 + uint16 < uint16).
        lw = _expr_width(node.left, sig_w)
        rw = _expr_width(node.right, sig_w)
        if lw < 64:
            l = f'({l} & {_mask(lw)})'
        if rw < 64:
            r = f'({r} & {_mask(rw)})'
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

# Module-level set of signal names promoted to C locals.
# When non-empty, _expr emits bare names and _emit_stmt omits 's->' prefix.
_c_locals: set = set()


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
        else:
            tgt = stmt.target if stmt.target in _c_locals else f's->{stmt.target}'
            if w and w < 64:
                lines.append(f'{pad}{tgt} = ({_ctype(w)})({val} & {_mask(w)});')
            else:
                lines.append(f'{pad}{tgt} = {val};')

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
        # If any case value is non-constant (Sig), emit as if-else chain
        has_non_const = any(not isinstance(v, Const) for v, _ in stmt.cases)
        if has_non_const:
            sel = _expr(stmt.sel, sig_w, pack_map)
            for i, (val, body) in enumerate(stmt.cases):
                kw = 'if' if i == 0 else '} else if'
                lines.append(f'{pad}{kw} ({sel} == {_expr(val, sig_w, pack_map)}) {{')
                for s in body:
                    _emit_stmt(s, lines, sig_w, indent + 1, pack_map, nba_sigs)
            if stmt.default:
                lines.append(f'{pad}}} else {{')
                for s in stmt.default:
                    _emit_stmt(s, lines, sig_w, indent + 1, pack_map, nba_sigs)
            lines.append(f'{pad}}}')
        else:
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


def _emit_stmts_batched(stmts, lines, sig_w, indent=1, pack_map=None, nba_sigs=None):
    """Emit statements, merging consecutive top-level packed writes to the same word.

    When multiple consecutive Assign statements write to 1-bit signals that
    share the same pack word, they are collapsed into a single read-modify-write
    instead of N separate RMW operations.
    """
    pad = '    ' * indent
    pending: dict = {}   # word_name → [(bit, val_expr_str)]
    pending_order: list = []  # word names in insertion order

    def _flush():
        for word in pending_order:
            writes = pending[word]
            if len(writes) == 1:
                bit, val = writes[0]
                lines.append(f'{pad}s->{word} = (s->{word} & ~(1ULL << {bit}ULL)) '
                             f'| (({val} & 1ULL) << {bit}ULL);')
            else:
                mask = sum(1 << b for b, _ in writes)
                val_parts = ' | '.join(f'(({v} & 1ULL) << {b}ULL)' for b, v in writes)
                lines.append(f'{pad}s->{word} = (s->{word} & ~{mask}ULL) | {val_parts};')
        pending.clear()
        pending_order.clear()

    for stmt in stmts:
        if (pack_map and isinstance(stmt, Assign)
                and stmt.target in pack_map
                and not (nba_sigs and stmt.target in nba_sigs)):
            word, bit = pack_map[stmt.target]
            val = _expr(stmt.value, sig_w, pack_map)
            if word not in pending:
                pending_order.append(word)
            pending.setdefault(word, []).append((bit, val))
        else:
            _flush()
            _emit_stmt(stmt, lines, sig_w, indent, pack_map, nba_sigs)
    _flush()


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

    Traces chains of continuous assigns *and* trivial comb blocks
    (single ``Assign(target, Sig(name))`` statements) where the RHS is
    a bare signal.  Stops when no further alias exists.
    """
    direct = {a.target: a.value.name for a in ir.assigns if isinstance(a.value, Sig)}
    for blk in ir.comb_blocks:
        if len(blk.stmts) == 1 and isinstance(blk.stmts[0], Assign):
            a = blk.stmts[0]
            if isinstance(a.value, Sig):
                direct.setdefault(a.target, a.value.name)
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

    # Collect candidates: wire written by exactly one block (a single-stmt Assign)
    # Count ALL writers (including Case/If blocks) to avoid inlining signals
    # that are also written inside compound statements.
    all_writers: dict = {}
    candidates: dict = {}
    for blk in ir.comb_blocks:
        ws: set = set()
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, ws, set())
        for name in ws:
            all_writers[name] = all_writers.get(name, 0) + 1
        if len(blk.stmts) == 1 and isinstance(blk.stmts[0], Assign):
            name = blk.stmts[0].target
            if name in wire_names:
                candidates[name] = blk.stmts[0].value

    candidates = {n: e for n, e in candidates.items() if all_writers.get(n, 0) == 1}

    # Drop candidates read by seq blocks (stmts or edges)
    seq_reads: set = set()
    for blk in ir.seq_blocks:
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, set(), seq_reads)
        for _, sig in blk.edges:
            seq_reads.add(sig)  # clock signals must stay in State struct
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


def _build_dirty_indices(sig_w: dict, mems=()) -> tuple:
    """Assign a dirty bit index to every signal in sig_w and every mem.

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
    for m in mems:
        dirty_idx[m.name] = (idx // 64, 1 << (idx % 64))
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
    dirty_idx, n_dirty_words = _build_dirty_indices(sig_w, ir.mems)

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
    # Must include transitive closure: if group A reads an NBA signal and
    # writes signal X, then group B that reads X must also re-settle.
    group_writes: list[set] = []
    for g in merge_groups:
        ws = set()
        for i in g:
            ws |= comb_writes[i]
        group_writes.append(ws)

    affected = set()
    # Seed frontier with NBA signals AND mem arrays written by seq blocks
    # (mem writes are immediate, not NBA-deferred, but still need re-settle)
    seq_mem_writes = set()
    mem_names = {m.name for m in ir.mems}
    for blk in ir.seq_blocks:
        ws = set()
        for stmt in blk.stmts:
            _swr(stmt, ws, set())
        seq_mem_writes |= ws & mem_names
    frontier = nba_sigs | seq_mem_writes
    changed = True
    while changed:
        changed = False
        for gi, g in enumerate(merge_groups):
            if gi in affected:
                continue
            if any(comb_deps[i] & frontier for i in g):
                affected.add(gi)
                frontier |= group_writes[gi]
                changed = True
    resettl_groups = [g for gi, g in enumerate(merge_groups) if gi in affected]
    lines = [
        '#include <stdint.h>',
        '#include <stdlib.h>',
        '#include <string.h>',
        '#include <stdio.h>',
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

    # ── Identify signals promotable to C locals ──────────────────
    # In monolithic eval, ALL signals that don't need to persist across
    # eval() calls can be C locals. Only ports, regs, mems, and NBA
    # targets must remain in the struct.
    _seq_reads = set()
    _seq_writes_all = set()
    for blk in ir.seq_blocks:
        for stmt in blk.stmts:
            _swr(stmt, _seq_writes_all, _seq_reads)
        for _, sig in blk.edges:
            _seq_reads.add(sig)
    _output_ports = {p.name for p in ir.ports if p.direction == 'output'}
    _input_ports = {p.name for p in ir.ports if p.direction == 'input'}
    _reg_names = {r.name for r in ir.regs}
    _mem_names_set = {m.name for m in ir.mems}
    must_persist = _input_ports | _output_ports | _reg_names | _mem_names_set
    promoted_locals = set(all_sigs) - must_persist - nba_sigs

    # Order fields by evaluation access pattern for spatial locality
    eval_order = _eval_order_sigs(ir)
    eval_rank = {name: i for i, name in enumerate(eval_order)}
    ordered_names = sorted(all_sigs, key=lambda n: eval_rank.get(n, len(eval_order)))

    # Pack 1-bit signals into uint64_t bitfield words
    # Exclude promoted locals from packing (they'll be C locals)
    struct_sigs = {n: w for n, w in all_sigs.items() if n not in promoted_locals}
    struct_ordered = [n for n in ordered_names if n not in promoted_locals]
    pack_map, pack_words = _build_pack_map(struct_sigs, struct_ordered)

    # Rebuild dirty indices excluding promoted locals
    dirty_idx, n_dirty_words = _build_dirty_indices(
        {n: w for n, w in sig_w.items() if n not in promoted_locals}, ir.mems)

    for name in struct_ordered:
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
    lines.append('static void _vcd_dump(State* s);')
    lines.append('static int _assert_fail = 0;')
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

    # ── Monolithic eval ─────────────────────────────────────────
    # All comb+seq+re-settle logic is emitted inline in veripy_eval
    # so that intermediate signals can be C locals (register-allocated).

    # Recompute promoted_locals: everything that doesn't need to persist
    promoted_locals = set(all_sigs) - must_persist - nba_sigs
    # Exclude packed signals (they use bitfield ops on struct words)
    promoted_locals -= set(pack_map.keys())

    _c_locals.clear()
    _c_locals.update(promoted_locals)

    lines.append('void veripy_eval(void* p) {')
    lines.append('    State* s = (State*)p;')

    # Declare promoted locals as C local variables
    for name in sorted(promoted_locals):
        w = all_sigs[name]
        lines.append(f'    {_ctype(w)} {name} = 0;')

    # ── Phase 1: Settle combinational logic (unconditional) ──────
    # Continuous assigns
    for a in ir.assigns:
        w = sig_w.get(a.target, 0)
        val = _expr(a.value, sig_w, pack_map)
        if pack_map and a.target in pack_map:
            lines.append(f'    {_pack_write(a.target, val, pack_map)}')
        elif w and w < 64:
            tgt = a.target if a.target in _c_locals else f's->{a.target}'
            lines.append(f'    {tgt} = ({_ctype(w)})({val} & {_mask(w)});')
        else:
            tgt = a.target if a.target in _c_locals else f's->{a.target}'
            lines.append(f'    {tgt} = {val};')

    # Comb blocks in topo order (all inlined)
    for gi, group in enumerate(merge_groups):
        all_stmts = []
        for idx in group:
            all_stmts.extend(ir.comb_blocks[idx].stmts)
        body = []
        _emit_stmts_batched(all_stmts, body, sig_w, pack_map=pack_map)
        lines.extend(body)

    # Clear dirty bits after initial comb settle
    lines.append(f'    memset(s->_dirty, 0, {n_dirty_words * 8});')

    # ── Phase 2: Sequential logic (edge-triggered) ──────────────
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
        # NBA snapshot
        group_nba = set()
        for idx in block_ids:
            group_nba |= nba_per_seq[idx]
        for name in sorted(group_nba):
            src = _pack_read(name, pack_map) if name in pack_map else f's->{name}'
            lines.append(f'        s->_nba_{name} = {src};')
        # Seq block bodies (inlined)
        for idx in block_ids:
            body = []
            _emit_stmts_batched(ir.seq_blocks[idx].stmts, body, sig_w,
                                pack_map=pack_map, nba_sigs=nba_sigs)
            lines.extend('    ' + ln for ln in body)
        # NBA commit
        for name in sorted(group_nba):
            if name in pack_map:
                lines.append(f'        {_pack_write(name, f"s->_nba_{name}", pack_map)}')
            else:
                lines.append(f'        s->{name} = s->_nba_{name};')
        # Mark seq outputs dirty
        group_seq_writes: set = set()
        for idx in block_ids:
            group_seq_writes |= seq_writes[idx]
        lines.extend(_dirty_set_lines(group_seq_writes, dirty_idx, indent=2))
        lines.append('    }')

    # ── Phase 3: Re-settle combinational logic (dirty-driven) ───
    for a in ir.assigns:
        w = sig_w.get(a.target, 0)
        val = _expr(a.value, sig_w, pack_map)
        if pack_map and a.target in pack_map:
            lines.append(f'    {_pack_write(a.target, val, pack_map)}')
        elif w and w < 64:
            tgt = a.target if a.target in _c_locals else f's->{a.target}'
            lines.append(f'    {tgt} = ({_ctype(w)})({val} & {_mask(w)});')
        else:
            tgt = a.target if a.target in _c_locals else f's->{a.target}'
            lines.append(f'    {tgt} = {val};')

    for group in resettl_groups:
        gi = merge_groups.index(group)
        all_stmts = []
        for idx in group:
            all_stmts.extend(ir.comb_blocks[idx].stmts)
        group_reads: set = set()
        group_writes: set = set()
        for idx in group:
            group_reads |= comb_deps[idx]
            group_writes |= comb_writes[idx]
        cond = _dirty_cond(group_reads, dirty_idx)
        out_lines = _dirty_set_lines(group_writes, dirty_idx, indent=2)
        if cond == '1':
            body = []
            _emit_stmts_batched(all_stmts, body, sig_w, pack_map=pack_map)
            lines.extend(body)
            lines.extend(out_lines)
        else:
            lines.append(f'    if ({cond}) {{')
            body = []
            _emit_stmts_batched(all_stmts, body, sig_w, pack_map=pack_map)
            lines.extend('    ' + b for b in body)
            lines.extend('    ' + ol for ol in out_lines)
            lines.append('    }')

    # ── Phase 4: Runtime assertions (before prev update) ────────
    for prop in ir.formal_props:
        if prop.kind != 'assert':
            continue
        clk = clock_aliases.get(prop.clock, prop.clock)
        clk_cur = _pack_read(clk, pack_map) if clk in pack_map else f's->{clk}'
        if prop.edge == 'posedge':
            edge_cond = f'({clk_cur} && !s->_prev_{clk})'
        else:
            edge_cond = f'(!{clk_cur} && s->_prev_{clk})'
        cond = _expr(prop.expr, sig_w, pack_map)
        lines.append(f'    if ({edge_cond} && !({cond})) {{')
        lines.append(f'        _assert_fail = 1;')
        lines.append(f'    }}')

    # ── Phase 5: Update previous values ──────────────────────────
    for clk in sorted(clocks):
        clk_expr = _pack_read(clk, pack_map) if clk in pack_map else f's->{clk}'
        lines.append(f'    s->_prev_{clk} = {clk_expr};')

    lines.append('    _vcd_dump(s);')
    lines.append('}')
    lines.append('')

    _c_locals.clear()

    # ── VCD trace support ────────────────────────────────────────
    # Build list of traceable signals: ports + regs (not C locals, not mems)
    trace_sigs = []  # (name, width, vcd_id)
    vcd_id = 33  # start at '!' (ASCII 33)
    for name in struct_ordered:
        if name.startswith('_prev_') or name.startswith('_nba_') or name.startswith('_dirty'):
            continue
        w = all_sigs.get(name, sig_w.get(name, 1))
        # VCD identifier: single or multi-char
        tid = ''
        v = vcd_id
        while True:
            tid = chr(33 + (v % 94)) + tid
            v = v // 94
            if v == 0:
                break
        trace_sigs.append((name, w, tid))
        vcd_id += 1

    n_trace = len(trace_sigs)
    lines.append(f'static FILE* _vcd_fp = 0;')
    lines.append(f'static int _vcd_enabled = 1;')
    lines.append(f'static uint64_t _vcd_prev[{n_trace}];')
    lines.append(f'static uint64_t _vcd_time = 0;')
    lines.append('')
    lines.append('int veripy_assert_failed(void) { return _assert_fail; }')
    lines.append('void veripy_assert_clear(void) { _assert_fail = 0; }')
    lines.append('')

    # VCD header writer
    lines.append('void veripy_trace_open(const char* path) {')
    lines.append('    _vcd_fp = fopen(path, "w");')
    lines.append('    if (!_vcd_fp) return;')
    lines.append('    fprintf(_vcd_fp, "$timescale 1ns $end\\n");')
    lines.append('    fprintf(_vcd_fp, "$scope module top $end\\n");')
    for name, w, tid in trace_sigs:
        lines.append(f'    fprintf(_vcd_fp, "$var wire {w} {tid} {name} $end\\n");')
    lines.append('    fprintf(_vcd_fp, "$upscope $end\\n");')
    lines.append('    fprintf(_vcd_fp, "$enddefinitions $end\\n");')
    lines.append(f'    memset(_vcd_prev, 0xFF, sizeof(_vcd_prev));')
    lines.append('    _vcd_time = 0;')
    lines.append('    _vcd_enabled = 1;')
    lines.append('}')
    lines.append('')

    lines.append('void veripy_trace_close(void) {')
    lines.append('    if (_vcd_fp) { fclose(_vcd_fp); _vcd_fp = 0; }')
    lines.append('}')
    lines.append('')

    lines.append('void veripy_trace_enable(int en) {')
    lines.append('    if (en && !_vcd_enabled)')
    lines.append(f'        memset(_vcd_prev, 0xFF, sizeof(_vcd_prev));  /* re-dump all on re-enable */')
    lines.append('    _vcd_enabled = en;')
    lines.append('}')
    lines.append('')

    # VCD dump function — called at end of each eval
    lines.append('static void _vcd_dump(State* s) {')
    lines.append('    if (!_vcd_fp || !_vcd_enabled) return;')
    lines.append('    int any = 0;')
    for i, (name, w, tid) in enumerate(trace_sigs):
        if name in pack_map:
            word, bit = pack_map[name]
            val_expr = f'((s->{word} >> {bit}ULL) & 1ULL)'
        else:
            val_expr = f's->{name}'
        lines.append(f'    {{ uint64_t v = {val_expr};')
        lines.append(f'      if (v != _vcd_prev[{i}]) {{')
        lines.append(f'        if (!any) {{ fprintf(_vcd_fp, "#%llu\\n", (unsigned long long)_vcd_time); any = 1; }}')
        if w == 1:
            lines.append(f'        fprintf(_vcd_fp, "%c{tid}\\n", (char)(\'0\' + (v & 1)));')
        else:
            lines.append(f'        fprintf(_vcd_fp, "b");')
            lines.append(f'        for (int _b = {w - 1}; _b >= 0; _b--) fprintf(_vcd_fp, "%c", (char)(\'0\' + ((v >> _b) & 1)));')
            lines.append(f'        fprintf(_vcd_fp, " {tid}\\n");')
        lines.append(f'        _vcd_prev[{i}] = v;')
        lines.append(f'    }} }}')
    lines.append('    _vcd_time++;')
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

    # ── Internal signal getters (regs, mems) ─────────────────────
    for r in ir.regs:
        if any(p.name == r.name for p in ir.ports):
            continue  # already emitted above
        w = _resolve_width(r.width, ir.params)
        if r.name in pack_map:
            word, bit = pack_map[r.name]
            lines.append(
                f'uint64_t veripy_get_{r.name}(void* p) '
                f'{{ return (((State*)p)->{word} >> {bit}ULL) & 1ULL; }}')
        else:
            lines.append(f'uint64_t veripy_get_{r.name}(void* p) '
                         f'{{ return ((State*)p)->{r.name}; }}')
        lines.append('')

    for m in ir.mems:
        w = _resolve_width(m.width, ir.params)
        lines.append(f'uint64_t veripy_get_{m.name}(void* p, uint64_t idx) '
                     f'{{ return ((State*)p)->{m.name}[idx]; }}')
        lines.append(f'void veripy_set_{m.name}(void* p, uint64_t idx, uint64_t v) '
                     f'{{ ((State*)p)->{m.name}[idx] = ({_ctype(w)})(v & {_mask(w)}); }}')
        lines.append('')

    return '\n'.join(lines) + '\n'

# ── Hierarchical (per-module) C emission ─────────────────────────────

def _c_ident(mod_type: str) -> str:
    """Sanitise a registry key into a valid C identifier."""
    return mod_type.replace('-', '_')


def _resolve_params_ir(ir: IRModule):
    """Resolve remaining Param nodes in an IR using ir.params.

    Mutates *ir* in place — replaces Param → Const where possible.
    """
    from .flatten import _rename_expr, _rename_stmt
    identity = {}
    params = ir.params
    if not params:
        return
    for i, a in enumerate(ir.assigns):
        ir.assigns[i] = ContAssign(a.target, _rename_expr(a.value, identity, params))
    for i, blk in enumerate(ir.comb_blocks):
        ir.comb_blocks[i] = CombBlock(
            stmts=[_rename_stmt(s, identity, params) for s in blk.stmts],
            locals=blk.locals)
    for i, blk in enumerate(ir.seq_blocks):
        ir.seq_blocks[i] = SeqBlock(
            edges=blk.edges,
            stmts=[_rename_stmt(s, identity, params) for s in blk.stmts],
            locals=blk.locals)


def emit_c_hier(top_ir: IRModule, registry: dict) -> str:
    """Emit C source for hierarchical (per-module) compilation.

    Optimised path: instances are topologically sorted within each module
    so that a single-pass comb evaluation suffices (no settle loop) when
    there are no combinational cycles.  Cycles (SCCs) get a minimal
    settle loop covering only the involved instances.

    After seq, only comb units whose inputs were dirtied are re-evaluated
    (dirty-bit selective re-settle).  1-bit signals are packed into
    ``uint64_t`` bitfields and struct fields are ordered by eval access
    for cache locality.
    """
    from copy import deepcopy

    # Deep-copy so we can mutate (resolve params) without affecting caller
    top_ir = deepcopy(top_ir)
    registry = {k: deepcopy(v) for k, v in registry.items()}

    # Resolve any remaining Param nodes
    _resolve_params_ir(top_ir)
    for ir in registry.values():
        _resolve_params_ir(ir)

    # Determine emission order: leaves first, top last
    order = []
    visited = set()

    def _visit(mod_type):
        if mod_type in visited:
            return
        visited.add(mod_type)
        ir = top_ir if mod_type == top_ir.name else registry[mod_type]
        for inst in ir.instances:
            _visit(inst.mod_type)
        order.append(mod_type)

    _visit(top_ir.name)

    # Identify leaf module types (no instances, no seq) for inlining
    leaf_types = set()
    for mod_type in order:
        ir = top_ir if mod_type == top_ir.name else registry[mod_type]
        if not ir.instances and not ir.seq_blocks:
            leaf_types.add(mod_type)

    lines = ['#include <stdint.h>', '#include <stdlib.h>',
             '#include <string.h>', '']

    # Forward-declare all State types
    for mod_type in order:
        cid = _c_ident(mod_type)
        lines.append(f'typedef struct State_{cid} State_{cid};')
    lines.append('')

    # Emit each module type (struct + comb + seq)
    emitted = set()
    for mod_type in order:
        if mod_type in emitted:
            continue
        emitted.add(mod_type)
        ir = top_ir if mod_type == top_ir.name else registry[mod_type]
        _emit_hier_module(ir, mod_type, registry, lines, leaf_types)

    # Top-level eval
    _emit_hier_eval(top_ir, lines)

    # create / destroy
    top_cid = _c_ident(top_ir.name)
    lines += [
        f'void* veripy_create(void) {{',
        f'    State_{top_cid}* s = calloc(1, sizeof(State_{top_cid}));',
        f'    memset(s->_dirty, 0xFF, sizeof(s->_dirty));',
        f'    return s;',
        f'}}', '',
        f'void veripy_destroy(void* p) {{ free(p); }}', '',
    ]

    # Per-port set/get API (top-level ports only)
    for p in top_ir.ports:
        w = _resolve_width(p.width, top_ir.params)
        if p.name in _hier_pack_maps.get(top_ir.name, {}):
            word, bit = _hier_pack_maps[top_ir.name][p.name]
            if p.direction == 'input':
                lines.append(
                    f'void veripy_set_{p.name}(void* p, uint64_t v) '
                    f'{{ State_{top_cid}* s = (State_{top_cid}*)p; '
                    f's->{word} = (s->{word} & ~(1ULL << {bit}ULL)) '
                    f'| ((v & 1ULL) << {bit}ULL); }}')
            lines.append(
                f'uint64_t veripy_get_{p.name}(void* p) '
                f'{{ return (((State_{top_cid}*)p)->{word} >> {bit}ULL) & 1ULL; }}')
        else:
            if p.direction == 'input':
                lines.append(
                    f'void veripy_set_{p.name}(void* p, uint64_t v) '
                    f'{{ (({_state_type(top_ir)}*)p)->{p.name} = '
                    f'({_ctype(w)})(v & {_mask(w)}); }}')
            lines.append(
                f'uint64_t veripy_get_{p.name}(void* p) '
                f'{{ return (({_state_type(top_ir)}*)p)->{p.name}; }}')
        lines.append('')

    return '\n'.join(lines) + '\n'

# Temporary storage for pack maps built during emission (keyed by mod name)
_hier_pack_maps: dict = {}


def _state_type(ir):
    return f'State_{_c_ident(ir.name)}'


# ── Hierarchical topo-sort + comb emission helpers ───────────────────

def _hier_topo_sort_units(ir, registry, sig_w):
    """Topologically sort evaluation units within a module.

    Returns a list of evaluation units, each being one of:
        ('assign', ContAssign)
        ('comb', CombBlock)
        ('instance', Instance)
        ('settle', [Instance, ...])   — SCC requiring settle loop

    Instances are ordered so that outputs of earlier instances are
    available as inputs to later ones (through continuous assigns).
    When combinational cycles exist (e.g. csrs ↔ traps), the involved
    instances are grouped into a settle unit.
    """
    from .flatten import _expr_reads, _stmt_writes_reads

    if not ir.instances:
        # No instances — just assigns then comb blocks, no sorting needed
        units = [('assign', a) for a in ir.assigns]
        units += [('comb', blk) for blk in ir.comb_blocks]
        return units

    # Build per-instance input/output wire sets
    inst_inputs = {}   # inst_name → set of parent wires read
    inst_outputs = {}  # inst_name → set of parent wires written
    inst_by_name = {}
    for inst in ir.instances:
        child_ir = registry.get(inst.mod_type)
        if not child_ir:
            continue
        inst_by_name[inst.inst_name] = inst
        dirs = {p.name: p.direction for p in child_ir.ports}
        inst_inputs[inst.inst_name] = {w for p, w in inst.ports if dirs.get(p) == 'input'}
        inst_outputs[inst.inst_name] = {w for p, w in inst.ports if dirs.get(p) == 'output'}

    # Build assign write→read map: which signals does each assign produce/consume
    assign_writes = {}  # target → ContAssign
    assign_reads = {}   # target → set of signals read
    for a in ir.assigns:
        assign_writes[a.target] = a
        assign_reads[a.target] = _expr_reads(a.value)

    # Build instance dependency graph through assigns:
    # inst A → inst B if A outputs a wire that (through assigns) feeds B's input
    def _trace_producers(sig, visited=None):
        """Find which instances produce a signal (transitively through assigns)."""
        if visited is None:
            visited = set()
        if sig in visited:
            return set()
        visited.add(sig)
        producers = set()
        for iname, outs in inst_outputs.items():
            if sig in outs:
                producers.add(iname)
        if sig in assign_reads:
            for dep_sig in assign_reads[sig]:
                producers |= _trace_producers(dep_sig, visited)
        return producers

    # Build adjacency: inst_name → set of inst_names it depends on
    deps = {iname: set() for iname in inst_by_name}
    for iname, inputs in inst_inputs.items():
        for wire in inputs:
            # Trace through assigns to find producing instances
            producers = _trace_producers(wire)
            for p in producers:
                if p != iname:
                    deps[iname].add(p)

    # Tarjan's SCC algorithm for topo sort with cycle detection
    index_counter = [0]
    stack = []
    on_stack = set()
    indices = {}
    lowlinks = {}
    sccs = []

    def _strongconnect(v):
        indices[v] = lowlinks[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(deps.get(v, [])):
            if w not in indices:
                _strongconnect(w)
                lowlinks[v] = min(lowlinks[v], lowlinks[w])
            elif w in on_stack:
                lowlinks[v] = min(lowlinks[v], indices[w])
        if lowlinks[v] == indices[v]:
            scc = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            sccs.append(scc)

    for v in sorted(inst_by_name.keys()):
        if v not in indices:
            _strongconnect(v)

    # sccs are in reverse topo order; reverse for forward order
    sccs.reverse()

    # Build the output wire set that's been "produced" so far
    produced = set()
    # All registers and ports are available at start
    for p in ir.ports:
        produced.add(p.name)
    for r in ir.regs:
        produced.add(r.name)

    units = []

    # For each SCC in topo order, emit the assigns needed, then the instance(s)
    for scc in sccs:
        # Collect all input wires needed by instances in this SCC
        needed = set()
        for iname in scc:
            needed |= inst_inputs.get(iname, set())

        # Emit assigns that produce needed wires (and their transitive deps)
        def _emit_assign_chain(target, emitted):
            if target in emitted or target in produced:
                return
            if target not in assign_writes:
                return
            emitted.add(target)
            # First emit dependencies
            for dep in assign_reads.get(target, set()):
                _emit_assign_chain(dep, emitted)
            units.append(('assign', assign_writes[target]))
            produced.add(target)

        emitted_assigns = set()
        for wire in sorted(needed):
            _emit_assign_chain(wire, emitted_assigns)

        if len(scc) == 1:
            units.append(('instance', inst_by_name[scc[0]]))
        else:
            # SCC: need settle loop — include assigns between SCC members
            settle_insts = [inst_by_name[n] for n in scc]
            units.append(('settle', settle_insts))

        # Mark outputs as produced
        for iname in scc:
            produced |= inst_outputs.get(iname, set())

    # Emit remaining assigns not yet emitted
    emitted_targets = {a.target for kind, a in units if kind == 'assign'}
    for a in ir.assigns:
        if a.target not in emitted_targets:
            units.append(('assign', a))

    # Emit comb blocks last
    for blk in ir.comb_blocks:
        units.append(('comb', blk))

    return units


def _emit_hier_comb_body(ir, registry, eval_units, sig_w, pack_map,
                         dirty_idx, leaf_types, lines, force=False):
    """Emit the body of a _comb or _comb_resettle function.

    When *force* is True, all units run unconditionally (initial settle).
    When False, units are guarded by dirty-bit checks (re-settle).
    """
    from .flatten import _expr_reads, _stmt_writes_reads

    for kind, data in eval_units:
        if kind == 'assign':
            a = data
            reads = _expr_reads(a.value)
            writes = {a.target}
            cond = '1' if force else _dirty_cond(reads, dirty_idx)
            val = _expr(a.value, sig_w, pack_map)
            w = sig_w.get(a.target, 0)
            if a.target in pack_map:
                stmt = f'    {_pack_write(a.target, val, pack_map)}'
            elif w and w < 64:
                stmt = f'    s->{a.target} = ({_ctype(w)})({val} & {_mask(w)});'
            else:
                stmt = f'    s->{a.target} = {val};'
            dirty_lines = _dirty_set_lines(writes & set(dirty_idx), dirty_idx)
            if cond == '1':
                lines.append(stmt)
                if not force:
                    lines.extend(dirty_lines)
            else:
                lines.append(f'    if ({cond}) {{')
                lines.append(f'    {stmt}')
                lines.extend(f'    {dl}' for dl in dirty_lines)
                lines.append(f'    }}')

        elif kind == 'comb':
            blk = data
            reads, writes = set(), set()
            for stmt in blk.stmts:
                _stmt_writes_reads(stmt, writes, reads)
            cond = '1' if force else _dirty_cond(reads, dirty_idx)
            body = []
            _emit_stmts_batched(blk.stmts, body, sig_w, pack_map=pack_map)
            dirty_lines = _dirty_set_lines(writes & set(dirty_idx), dirty_idx)
            if cond == '1':
                lines.extend(body)
                if not force:
                    lines.extend(dirty_lines)
            else:
                lines.append(f'    if ({cond}) {{')
                lines.extend(f'    {b}' for b in body)
                lines.extend(f'    {dl}' for dl in dirty_lines)
                lines.append(f'    }}')

        elif kind == 'instance':
            inst = data
            _emit_one_instance(inst, ir, registry, sig_w, pack_map,
                               dirty_idx, leaf_types, lines, force)

        elif kind == 'settle':
            instances = data
            # Collect all wires involved in the SCC for dirty tracking
            scc_reads, scc_writes = set(), set()
            for inst in instances:
                child_ir = registry.get(inst.mod_type)
                if not child_ir:
                    continue
                dirs = {p.name: p.direction for p in child_ir.ports}
                for pname, wname in inst.ports:
                    if dirs.get(pname) == 'input':
                        scc_reads.add(wname)
                    elif dirs.get(pname) == 'output':
                        scc_writes.add(wname)

            cond = '1' if force else _dirty_cond(scc_reads, dirty_idx)
            if cond != '1':
                lines.append(f'    if ({cond}) {{')

            # Settle loop for SCC — 2 iterations
            pad = '    ' if cond != '1' else ''
            lines.append(f'{pad}    for (int _settle = 0; _settle < 2; _settle++) {{')
            # Emit assigns between SCC instances
            scc_names = {inst.inst_name for inst in instances}
            scc_output_wires = set()
            for inst in instances:
                child_ir = registry.get(inst.mod_type)
                if child_ir:
                    dirs = {p.name: p.direction for p in child_ir.ports}
                    scc_output_wires |= {w for p, w in inst.ports if dirs.get(p) == 'output'}
            for a in ir.assigns:
                reads = _expr_reads(a.value)
                if reads & scc_output_wires:
                    val = _expr(a.value, sig_w, pack_map)
                    w = sig_w.get(a.target, 0)
                    if a.target in pack_map:
                        lines.append(f'{pad}        {_pack_write(a.target, val, pack_map)}')
                    elif w and w < 64:
                        lines.append(f'{pad}        s->{a.target} = ({_ctype(w)})({val} & {_mask(w)});')
                    else:
                        lines.append(f'{pad}        s->{a.target} = {val};')
            for inst in instances:
                _emit_one_instance(inst, ir, registry, sig_w, pack_map,
                                   dirty_idx, leaf_types, lines, force=True,
                                   extra_indent=pad + '    ')
            lines.append(f'{pad}    }}')
            if not force:
                lines.extend(_dirty_set_lines(scc_writes & set(dirty_idx), dirty_idx))
            if cond != '1':
                lines.append(f'    }}')


def _emit_one_instance(inst, parent_ir, registry, sig_w, pack_map,
                       dirty_idx, leaf_types, lines, force=False,
                       extra_indent=''):
    """Emit copy-in, comb eval, copy-out for a single instance.

    For leaf modules, inlines the child's comb logic directly.
    """
    child_ir = registry.get(inst.mod_type)
    if not child_ir:
        return
    child_dirs = {p.name: p.direction for p in child_ir.ports}
    child_cid = _c_ident(inst.mod_type)
    pad = extra_indent + '    '

    # Collect input/output wires for dirty tracking
    input_wires = {w for p, w in inst.ports if child_dirs.get(p) == 'input'}
    output_wires = {w for p, w in inst.ports if child_dirs.get(p) == 'output'}

    cond = '1' if force else _dirty_cond(input_wires, dirty_idx)

    if cond != '1':
        lines.append(f'{pad}if ({cond}) {{')
        ipad = pad + '    '
    else:
        ipad = pad

    # Copy inputs
    for pname, wname in inst.ports:
        if child_dirs.get(pname) == 'input':
            src = _pack_read(wname, pack_map) if wname in pack_map else f's->{wname}'
            child_pack = _hier_pack_maps.get(inst.mod_type, {})
            if pname in child_pack:
                word, bit = child_pack[pname]
                lines.append(f'{ipad}s->{inst.inst_name}.{word} = '
                             f'(s->{inst.inst_name}.{word} & ~(1ULL << {bit}ULL)) '
                             f'| (({src} & 1ULL) << {bit}ULL);')
            else:
                lines.append(f'{ipad}s->{inst.inst_name}.{pname} = {src};')

    # Eval: call child _comb (leaf modules are already always_inline)
    # In re-settle mode (force=False), call _comb_resettle if the child
    # has one (modules with instances or seq blocks), avoiding full
    # re-evaluation when only some inputs changed.
    child_cid = _c_ident(inst.mod_type)
    has_resettle = child_ir.instances or child_ir.seq_blocks
    if not force and has_resettle:
        lines.append(f'{ipad}_comb_resettle_{child_cid}(&s->{inst.inst_name});')
    else:
        lines.append(f'{ipad}_comb_{child_cid}(&s->{inst.inst_name});')

    # Copy outputs
    for pname, wname in inst.ports:
        if child_dirs.get(pname) == 'output':
            child_pack = _hier_pack_maps.get(inst.mod_type, {})
            if pname in child_pack:
                src = _pack_read(pname, child_pack)
                src = src.replace('s->', f's->{inst.inst_name}.')
            else:
                src = f's->{inst.inst_name}.{pname}'
            if wname in pack_map:
                lines.append(f'{ipad}{_pack_write(wname, src, pack_map)}')
            else:
                lines.append(f'{ipad}s->{wname} = {src};')

    # Mark output wires dirty
    if not force:
        dirty_lines = _dirty_set_lines(output_wires & set(dirty_idx), dirty_idx)
        lines.extend(f'{ipad}{dl.strip()}' for dl in dirty_lines)

    if cond != '1':
        lines.append(f'{pad}}}')


def _emit_hier_module(ir, mod_type, registry, lines, leaf_types=None):
    """Emit State struct + _comb + _seq for one module type.

    Instances are topologically sorted by data dependencies so that a
    single-pass comb evaluation suffices.  Combinational cycles (SCCs)
    get a minimal settle loop covering only the involved instances.
    Leaf modules (no instances, no seq) are inlined into the parent.
    1-bit signals are packed into uint64_t bitfields.
    """
    if leaf_types is None:
        leaf_types = set()
    cid = _c_ident(mod_type)
    sig_w = _build_sig_widths(ir)
    nba_sigs, nba_per_seq = _collect_nba_signals(ir)

    # ── Collect all signals ──────────────────────────────────────
    all_sigs = {}
    for p in ir.ports:
        all_sigs[p.name] = _resolve_width(p.width, ir.params)
    for d in ir.wires:
        all_sigs[d.name] = _resolve_width(d.width, ir.params)
    for d in ir.regs:
        all_sigs[d.name] = _resolve_width(d.width, ir.params)
    for blk in ir.comb_blocks:
        for name, width in blk.locals.items():
            all_sigs.setdefault(name, _resolve_width(width, ir.params))
    for blk in ir.seq_blocks:
        for name, width in blk.locals.items():
            all_sigs.setdefault(name, _resolve_width(width, ir.params))

    # ── Signal packing + eval-order layout ───────────────────────
    ordered_names = sorted(all_sigs.keys())
    pack_map, pack_words = _build_pack_map(all_sigs, ordered_names)
    # Store for use by emit_c_hier set/get API
    _hier_pack_maps[mod_type] = pack_map

    # ── Dirty-bit indices ────────────────────────────────────────
    dirty_idx, n_dirty_words = _build_dirty_indices(sig_w, ir.mems)

    # ── State struct ─────────────────────────────────────────────
    lines.append(f'struct State_{cid} {{')

    for name in ordered_names:
        if name not in pack_map:
            lines.append(f'    {_ctype(all_sigs[name])} {name};')
    for pw in pack_words:
        lines.append(f'    uint64_t {pw};')

    # Memory arrays
    for m in ir.mems:
        if isinstance(m, MemDecl):
            w = _resolve_width(m.width, ir.params)
            d = _resolve_width(m.depth, ir.params) if isinstance(m.depth, str) else m.depth
            lines.append(f'    {_ctype(w)} {m.name}[{d}];')

    # Sub-module instances
    for inst in ir.instances:
        child_cid = _c_ident(inst.mod_type)
        lines.append(f'    State_{child_cid} {inst.inst_name};')

    # Edge detection prev values
    clocks = set()
    for blk in ir.seq_blocks:
        for edge_kind, sig_name in blk.edges:
            clocks.add(sig_name)
    for clk in sorted(clocks):
        lines.append(f'    uint8_t _prev_{clk};')

    # NBA temporaries
    for name in sorted(nba_sigs):
        w = all_sigs.get(name, 32)
        lines.append(f'    {_ctype(w)} _nba_{name};')

    # Dirty bits
    lines.append(f'    uint64_t _dirty[{n_dirty_words}];')

    lines.append(f'}};')
    lines.append('')

    # ── Topo-sort evaluation units ───────────────────────────────
    eval_units = _hier_topo_sort_units(ir, registry, sig_w)

    # ── _comb function ───────────────────────────────────────────
    inline = ' __attribute__((always_inline))' if not ir.instances else ''
    lines.append(f'static inline void _comb_{cid}(State_{cid}* s){inline} {{')
    _emit_hier_comb_body(ir, registry, eval_units, sig_w, pack_map,
                         dirty_idx, leaf_types, lines, force=True)
    lines.append('}')
    lines.append('')

    # ── _comb_resettle function (dirty-driven) ───────────────────
    if ir.instances or ir.seq_blocks:
        lines.append(f'static void _comb_resettle_{cid}(State_{cid}* s) {{')
        _emit_hier_comb_body(ir, registry, eval_units, sig_w, pack_map,
                             dirty_idx, leaf_types, lines, force=False)
        lines.append('}')
        lines.append('')

    # ── _seq function ────────────────────────────────────────────
    inline_s = ' __attribute__((always_inline))' if not ir.instances else ''
    lines.append(f'static inline void _seq_{cid}(State_{cid}* s){inline_s} {{')

    # Group seq blocks by edge
    edge_blocks = {}
    for i, blk in enumerate(ir.seq_blocks):
        for edge_kind, sig_name in blk.edges:
            edge_blocks.setdefault((edge_kind, sig_name), []).append(i)

    for (edge_kind, clk), block_ids in sorted(edge_blocks.items()):
        clk_expr = _pack_read(clk, pack_map) if clk in pack_map else f's->{clk}'
        if edge_kind == 'posedge':
            cond = f'{clk_expr} && !s->_prev_{clk}'
        else:
            cond = f'!{clk_expr} && s->_prev_{clk}'
        lines.append(f'    if ({cond}) {{')

        # NBA snapshot
        group_nba = set()
        for idx in block_ids:
            group_nba |= nba_per_seq[idx]
        for name in sorted(group_nba):
            src = _pack_read(name, pack_map) if name in pack_map else f's->{name}'
            lines.append(f'        s->_nba_{name} = {src};')

        # Seq blocks
        for idx in block_ids:
            body = []
            _emit_stmts_batched(ir.seq_blocks[idx].stmts, body, sig_w,
                                pack_map=pack_map, nba_sigs=nba_sigs)
            lines.extend('    ' + ln for ln in body)

        # NBA commit + mark dirty
        for name in sorted(group_nba):
            if name in pack_map:
                lines.append(f'        {_pack_write(name, f"s->_nba_{name}", pack_map)}')
            else:
                lines.append(f'        s->{name} = s->_nba_{name};')
        # Mark seq-written signals dirty for re-settle
        from .flatten import _stmt_writes_reads as _swr
        group_seq_writes = set()
        for idx in block_ids:
            ws = set()
            for stmt in ir.seq_blocks[idx].stmts:
                _swr(stmt, ws, set())
            group_seq_writes |= ws & set(dirty_idx)
        lines.extend(_dirty_set_lines(group_seq_writes, dirty_idx, indent=2))

        lines.append('    }')

    # Sub-module seq
    for inst in ir.instances:
        child_ir = registry.get(inst.mod_type)
        if not child_ir:
            continue
        if child_ir.seq_blocks or child_ir.instances:
            child_cid = _c_ident(inst.mod_type)
            lines.append(f'    _seq_{child_cid}(&s->{inst.inst_name});')

    # Update prev values
    for clk in sorted(clocks):
        clk_expr = _pack_read(clk, pack_map) if clk in pack_map else f's->{clk}'
        lines.append(f'    s->_prev_{clk} = {clk_expr};')

    lines.append('}')
    lines.append('')


def _emit_hier_eval(top_ir, lines):
    """Emit veripy_eval() for the top-level module.

    1. Full comb settle (unconditional — runs all units).
    2. Clear dirty bits.
    3. Seq (marks dirty bits for NBA-committed signals).
    4. Dirty-driven comb re-settle (only affected units).
    """
    cid = _c_ident(top_ir.name)
    pack_map = _hier_pack_maps.get(top_ir.name, {})
    sig_w = _build_sig_widths(top_ir)
    dirty_idx, n_dirty_words = _build_dirty_indices(sig_w, top_ir.mems)
    lines += [
        'void veripy_eval(void* p) {',
        f'    State_{cid}* s = (State_{cid}*)p;',
        f'    _comb_{cid}(s);',
        f'    memset(s->_dirty, 0, {n_dirty_words * 8});',
        f'    _seq_{cid}(s);',
        f'    _comb_resettle_{cid}(s);',
        '}', '',
    ]


# ── Compile + load ───────────────────────────────────────────────────

def compile_module(module, module_name=None, force_hier=False):
    """Compile a VeriPy Module to a CSimModel.

    Uses hierarchical per-module compilation when the design has
    sub-module instances (or force_hier=True), flat compilation otherwise.
    """
    from .lower import lower_module

    if module_name is None:
        module_name = type(module).__name__.lower()

    registry, patch_fn = _collect_submodule_registry(module)
    top_ir = lower_module(module, module_name)
    patch_fn(top_ir)

    if top_ir.instances or force_hier:
        return CSimModel(top_ir, registry=registry)
    return CSimModel(top_ir)


class CSimModel:
    """Compile C source to shared lib and wrap with ctypes.

    Same API as VerilatorModel: set/get/eval/step/close.
    """

    def __init__(self, ir: IRModule, build_dir=None, registry=None, trace=None):
        self._ptr = None
        self._tmpdir = None
        self._lib = None

        if registry is not None:
            # Hierarchical path — per-module compilation
            c_src = emit_c_hier(ir, registry)
        else:
            # Flat path — legacy single-module compilation
            from .flatten import topo_sort_comb
            if ir.instances:
                raise ValueError('IR must be flattened before CSimModel '
                                 '(call flatten_ir first)')
            ir = topo_sort_comb(ir)
            ir = _inline_cont_assigns(ir)
            c_src = emit_c(ir)

        self._signals = {}
        for p in ir.ports:
            w = _resolve_width(p.width, ir.params)
            self._signals[p.name] = (p.direction, w)

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

        # Internal signal getters (regs, mems)
        for r in ir.regs:
            if r.name in self._getters:
                continue
            fn = getattr(self._lib, f'veripy_get_{r.name}')
            fn.restype = ctypes.c_uint64
            fn.argtypes = [ctypes.c_void_p]
            self._getters[r.name] = fn

        self._mem_getters = {}
        self._mem_setters = {}
        for m in ir.mems:
            fn = getattr(self._lib, f'veripy_get_{m.name}')
            fn.restype = ctypes.c_uint64
            fn.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
            self._mem_getters[m.name] = fn
            fn = getattr(self._lib, f'veripy_set_{m.name}')
            fn.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64]
            self._mem_setters[m.name] = fn

        # VCD trace
        self._lib.veripy_trace_open.argtypes = [ctypes.c_char_p]
        self._lib.veripy_trace_close.argtypes = []
        self._lib.veripy_trace_enable.argtypes = [ctypes.c_int]
        self._lib.veripy_assert_failed.restype = ctypes.c_int
        self._lib.veripy_assert_clear.argtypes = []

        if trace:
            self.trace_open(trace)

    def trace_open(self, path):
        self._lib.veripy_trace_open(path.encode() if isinstance(path, str) else path)

    def trace_close(self):
        self._lib.veripy_trace_close()

    def trace_enable(self, enabled: bool):
        self._lib.veripy_trace_enable(int(enabled))

    def assert_failed(self):
        return bool(self._lib.veripy_assert_failed())

    def assert_clear(self):
        self._lib.veripy_assert_clear()

    def set(self, name, val, idx=None):
        if idx is not None:
            self._mem_setters[name](self._ptr, idx, val)
        else:
            self._setters[name](self._ptr, val)

    def load_mem(self, name, data, offset=0):
        """Load data into a memory array. data is an iterable of int values."""
        setter = self._mem_setters[name]
        for i, val in enumerate(data):
            setter(self._ptr, offset + i, val)

    def get(self, name, idx=None):
        if idx is not None:
            return self._mem_getters[name](self._ptr, idx)
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
            self.trace_close()
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
    registry, patch_fn = _collect_submodule_registry(module)

    top_ir = lower_module(module, module_name)
    patch_fn(top_ir)
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
    pgo = os.environ.get('VERIPY_PGO', '0') == '1'

    if pgo:
        # Pass 1: instrument for profiling
        prof_dir = os.path.join(build_dir, 'pgo')
        os.makedirs(prof_dir, exist_ok=True)
        r1 = subprocess.run(
            [cc, '-O3', '-march=native', '-fPIC', flag,
             '-fprofile-generate=' + prof_dir,
             '-o', lib_path, c_path],
            capture_output=True, text=True)
        if r1.returncode != 0:
            raise RuntimeError(f'PGO pass-1 failed:\n{r1.stderr}')
        # Run once to collect profile data
        lib_tmp = ctypes.CDLL(lib_path)
        lib_tmp.run_bench.restype = ctypes.c_uint64
        lib_tmp.run_bench()
        del lib_tmp
        # Pass 2: optimise with profile data
        r = subprocess.run(
            [cc, '-O3', '-march=native', '-flto', '-fPIC', flag,
             '-fprofile-use=' + prof_dir,
             '-o', lib_path, c_path],
            capture_output=True, text=True)
    else:
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
