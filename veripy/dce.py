"""Dead code elimination and constant propagation for flat IR.

Operates on a *flat, topo-sorted* IRModule (after ``topo_sort_comb``).
"""

from copy import deepcopy

from .ir import (
    Const, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    CombBlock, SeqBlock, IRModule,
)
from .flatten import _expr_reads, _stmt_writes_reads


# ── Constant propagation ────────────────────────────────────────────

def _try_fold(node):
    """Try to constant-fold an expression.  Returns *node* unchanged if
    folding is not possible."""
    if isinstance(node, Const):
        return node
    if isinstance(node, BinOp):
        l, r = _try_fold(node.left), _try_fold(node.right)
        if isinstance(l, Const) and isinstance(r, Const):
            a, b = l.value, r.value
            ops = {'+': lambda: a + b, '-': lambda: a - b,
                   '*': lambda: a * b, '&': lambda: a & b,
                   '|': lambda: a | b, '^': lambda: a ^ b,
                   '<<': lambda: a << b, '>>': lambda: a >> b}
            fn = ops.get(node.op)
            if fn is not None:
                return Const(fn())
        return BinOp(node.op, l, r)
    if isinstance(node, UnaryOp):
        operand = _try_fold(node.operand)
        if isinstance(operand, Const):
            if node.op == '~':
                return Const(~operand.value)
            if node.op == '!':
                return Const(int(not operand.value))
        return UnaryOp(node.op, operand)
    if isinstance(node, Compare):
        l, r = _try_fold(node.left), _try_fold(node.right)
        if isinstance(l, Const) and isinstance(r, Const):
            a, b = l.value, r.value
            ops = {'==': a == b, '!=': a != b, '<': a < b,
                   '>': a > b, '<=': a <= b, '>=': a >= b}
            if node.op in ops:
                return Const(int(ops[node.op]))
        return Compare(node.op, l, r)
    if isinstance(node, BoolOp):
        vals = [_try_fold(v) for v in node.values]
        if all(isinstance(v, Const) for v in vals):
            if node.op == '&&':
                return Const(int(all(v.value for v in vals)))
            if node.op == '||':
                return Const(int(any(v.value for v in vals)))
        return BoolOp(node.op, vals)
    if isinstance(node, Mux):
        sel = _try_fold(node.sel)
        if isinstance(sel, Const):
            return _try_fold(node.true_val) if sel.value else _try_fold(node.false_val)
        return Mux(sel, _try_fold(node.true_val), _try_fold(node.false_val))
    if isinstance(node, Slice):
        return Slice(_try_fold(node.signal),
                     _try_fold(node.hi) if node.hi else None,
                     _try_fold(node.lo))
    if isinstance(node, Index):
        return Index(_try_fold(node.signal), _try_fold(node.idx))
    if isinstance(node, Concat):
        return Concat([_try_fold(p) for p in node.parts])
    return node


def _subst_expr(node, const_map):
    """Replace Sig references with constants from *const_map*, then fold."""
    if isinstance(node, Sig):
        return const_map.get(node.name, node)
    if isinstance(node, (Const,)):
        return node
    if isinstance(node, BinOp):
        return _try_fold(BinOp(node.op,
                               _subst_expr(node.left, const_map),
                               _subst_expr(node.right, const_map)))
    if isinstance(node, UnaryOp):
        return _try_fold(UnaryOp(node.op, _subst_expr(node.operand, const_map)))
    if isinstance(node, Compare):
        return _try_fold(Compare(node.op,
                                 _subst_expr(node.left, const_map),
                                 _subst_expr(node.right, const_map)))
    if isinstance(node, BoolOp):
        return _try_fold(BoolOp(node.op,
                                [_subst_expr(v, const_map) for v in node.values]))
    if isinstance(node, Mux):
        return _try_fold(Mux(_subst_expr(node.sel, const_map),
                             _subst_expr(node.true_val, const_map),
                             _subst_expr(node.false_val, const_map)))
    if isinstance(node, Slice):
        return Slice(_subst_expr(node.signal, const_map),
                     _subst_expr(node.hi, const_map) if node.hi else None,
                     _subst_expr(node.lo, const_map))
    if isinstance(node, Index):
        return Index(_subst_expr(node.signal, const_map),
                     _subst_expr(node.idx, const_map))
    if isinstance(node, Concat):
        return Concat([_subst_expr(p, const_map) for p in node.parts])
    return node


def _subst_stmt(stmt, const_map):
    """Substitute constants in a statement's expressions."""
    sub = lambda n: _subst_expr(n, const_map)
    if isinstance(stmt, Assign):
        return Assign(stmt.target, sub(stmt.value), stmt.blocking)
    if isinstance(stmt, SliceAssign):
        return SliceAssign(stmt.target, sub(stmt.hi), sub(stmt.lo),
                           sub(stmt.value), stmt.blocking)
    if isinstance(stmt, MemWrite):
        return MemWrite(stmt.mem, sub(stmt.addr), sub(stmt.data), stmt.blocking)
    if isinstance(stmt, If):
        cond = sub(stmt.cond)
        # Fold dead branches
        if isinstance(cond, Const):
            if cond.value:
                return [_subst_stmt(s, const_map) for s in stmt.then_body]
            return [_subst_stmt(s, const_map) for s in stmt.else_body]
        return If(cond,
                  [_subst_stmt(s, const_map) for s in stmt.then_body],
                  [_subst_stmt(s, const_map) for s in stmt.else_body])
    if isinstance(stmt, Case):
        sel = sub(stmt.sel)
        cases = [(sub(v), [_subst_stmt(s, const_map) for s in body])
                 for v, body in stmt.cases]
        default = [_subst_stmt(s, const_map) for s in stmt.default] if stmt.default else None
        return Case(sel, cases, default)
    return stmt


def _flatten_stmts(stmts):
    """Flatten nested lists produced by dead-branch elimination."""
    out = []
    for s in stmts:
        if isinstance(s, list):
            out.extend(_flatten_stmts(s))
        else:
            out.append(s)
    return out


def const_propagate(ir: IRModule) -> IRModule:
    """Substitute known-constant signals and fold expressions.

    A signal is constant if it is written by exactly one comb_block that
    contains a single unconditional ``Assign(target, Const(v))`` and is
    not written by any seq_block.
    """
    # Find signals written by seq_blocks (not safe to propagate)
    seq_written = set()
    for blk in ir.seq_blocks:
        w = set()
        for s in blk.stmts:
            _stmt_writes_reads(s, w, set())
        seq_written |= w

    # Find single-assign constant comb_blocks
    # writer_count tracks how many comb_blocks write each signal
    writer_count = {}
    const_map = {}
    for blk in ir.comb_blocks:
        if (len(blk.stmts) == 1 and isinstance(blk.stmts[0], Assign)
                and isinstance(blk.stmts[0].value, Const)):
            name = blk.stmts[0].target
            writer_count[name] = writer_count.get(name, 0) + 1
            const_map[name] = blk.stmts[0].value
        else:
            w = set()
            for s in blk.stmts:
                _stmt_writes_reads(s, w, set())
            for name in w:
                writer_count[name] = writer_count.get(name, 0) + 1

    # Keep only signals with exactly one writer and not written by seq
    const_map = {k: v for k, v in const_map.items()
                 if writer_count.get(k) == 1 and k not in seq_written}

    if not const_map:
        return ir

    out = deepcopy(ir)
    # Substitute in comb_blocks
    for i, blk in enumerate(out.comb_blocks):
        new_stmts = _flatten_stmts([_subst_stmt(s, const_map) for s in blk.stmts])
        out.comb_blocks[i] = CombBlock(stmts=new_stmts, locals=blk.locals)
    # Substitute in seq_blocks
    for i, blk in enumerate(out.seq_blocks):
        new_stmts = _flatten_stmts([_subst_stmt(s, const_map) for s in blk.stmts])
        out.seq_blocks[i] = SeqBlock(edges=blk.edges, stmts=new_stmts,
                                     locals=blk.locals)
    return out


# ── Dead code elimination ────────────────────────────────────────────

def dead_code_eliminate(ir: IRModule) -> IRModule:
    """Remove comb_blocks whose outputs are never used.

    A signal is *live* if it is:
    - an output port,
    - read by any seq_block, or
    - read by a live comb_block.

    Iterates to a fixed point.
    """
    output_names = {p.name for p in ir.ports if p.direction == 'output'}

    # Collect reads from seq_blocks (always live)
    seq_reads = set()
    for blk in ir.seq_blocks:
        for s in blk.stmts:
            _stmt_writes_reads(s, set(), seq_reads)
        # Edge clock signals are live
        for _, sig in blk.edges:
            seq_reads.add(sig)

    live = output_names | seq_reads

    # Build per-block writes and reads
    blk_writes = []
    blk_reads = []
    for blk in ir.comb_blocks:
        w, r = set(), set()
        for s in blk.stmts:
            _stmt_writes_reads(s, w, r)
        blk_writes.append(w)
        blk_reads.append(r)

    # Fixed-point: mark blocks live if they write a live signal
    changed = True
    live_blocks = set()
    while changed:
        changed = False
        for i, (w, r) in enumerate(zip(blk_writes, blk_reads)):
            if i not in live_blocks and w & live:
                live_blocks.add(i)
                new = r - live
                if new:
                    live |= new
                    changed = True

    if len(live_blocks) == len(ir.comb_blocks):
        return ir  # nothing to prune

    out = deepcopy(ir)
    out.comb_blocks = [deepcopy(ir.comb_blocks[i])
                       for i in sorted(live_blocks)]

    # Prune dead wire/reg declarations
    declared_needed = set()
    for blk in out.comb_blocks:
        w, r = set(), set()
        for s in blk.stmts:
            _stmt_writes_reads(s, w, r)
        declared_needed |= w | r
    for blk in out.seq_blocks:
        w, r = set(), set()
        for s in blk.stmts:
            _stmt_writes_reads(s, w, r)
        declared_needed |= w | r
        for _, sig in blk.edges:
            declared_needed.add(sig)
    # Ports are always kept
    for p in ir.ports:
        declared_needed.add(p.name)

    out.wires = [w for w in out.wires if w.name in declared_needed]
    out.regs = [r for r in out.regs if r.name in declared_needed]

    return out


# ── Combined pass ────────────────────────────────────────────────────

def optimize(ir: IRModule) -> IRModule:
    """Run constant propagation then dead code elimination."""
    ir = const_propagate(ir)
    ir = dead_code_eliminate(ir)
    return ir
