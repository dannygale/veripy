"""Flatten sub-module instances into a single-level IRModule and
topologically sort combinational assignments.

Used by the WGSL backend and native C-sim backend.
"""

from collections import deque
from copy import deepcopy
from .ir import (
    Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock, InitialBlock, AlwaysBlock,
    FormalProperty, IRModule,
    Port, WireDecl, RegDecl, MemDecl, DualPortMemDecl, TrueDualPortMemDecl,
)


def flatten_ir(parent: IRModule, registry: dict[str, IRModule],
               params: dict | None = None) -> IRModule:
    """Return a new IRModule with all instances inlined (recursively).

    Args:
        parent: top-level module (not mutated)
        registry: mod_type → IRModule for each child type
        params: resolved parent params
    """
    out = deepcopy(parent)
    out.instances = []
    params = params or out.params

    for inst in parent.instances:
        child = registry.get(inst.mod_type)
        if child is None:
            raise ValueError(f'Unknown sub-module type: {inst.mod_type!r}')

        # Resolve child params
        child_params = dict(child.params)
        for k, v in inst.params.items():
            child_params[k] = params.get(v, v) if isinstance(v, str) else v

        # Recursively flatten child if it has instances
        if child.instances:
            child = flatten_ir(child, registry, child_params)
        else:
            child = deepcopy(child)

        # Build rename map: child signal name → flattened name
        prefix = inst.inst_name + '_'
        port_map = {pname: wname for pname, wname in inst.ports}
        rename = {}
        for p in child.ports:
            rename[p.name] = port_map.get(p.name, prefix + p.name)
        for r in child.regs:
            rename[r.name] = prefix + r.name
        for w in child.wires:
            rename[w.name] = prefix + w.name
        for m in child.mems:
            rename[m.name] = prefix + m.name

        # Add child internal signals (non-port) to parent
        child_port_names = {p.name for p in child.ports}
        for r in child.regs:
            if r.name not in child_port_names:
                out.regs.append(RegDecl(prefix + r.name, r.width))
        for w in child.wires:
            if w.name not in child_port_names:
                out.wires.append(WireDecl(prefix + w.name, w.width))
        for m in child.mems:
            if isinstance(m, TrueDualPortMemDecl):
                out.mems.append(TrueDualPortMemDecl(
                    prefix + m.name, m.depth, m.width, style=m.style,
                    clka=_rn(m.clka, rename), wea=_rn(m.wea, rename),
                    addra=_rn(m.addra, rename), dina=_rn(m.dina, rename),
                    douta=_rn(m.douta, rename),
                    clkb=_rn(m.clkb, rename), web=_rn(m.web, rename),
                    addrb=_rn(m.addrb, rename), dinb=_rn(m.dinb, rename),
                    doutb=_rn(m.doutb, rename)))
            elif isinstance(m, DualPortMemDecl):
                out.mems.append(DualPortMemDecl(
                    prefix + m.name, m.depth, m.width, style=m.style,
                    clock=_rn(m.clock, rename), we=_rn(m.we, rename),
                    waddr=_rn(m.waddr, rename), wdata=_rn(m.wdata, rename),
                    raddr=_rn(m.raddr, rename), rdata=_rn(m.rdata, rename)))
            else:
                out.mems.append(MemDecl(prefix + m.name, m.depth, m.width,
                                        style=m.style))

        # Inline assigns
        for a in child.assigns:
            out.assigns.append(ContAssign(
                _rn(a.target, rename), _rename_expr(a.value, rename)))

        # Inline comb blocks
        for blk in child.comb_blocks:
            loc = {_rn(k, rename): v for k, v in blk.locals.items()}
            out.comb_blocks.append(CombBlock(
                stmts=[_rename_stmt(s, rename) for s in blk.stmts],
                locals=loc))

        # Inline seq blocks
        for blk in child.seq_blocks:
            edges = [(kind, _rn(sig, rename)) for kind, sig in blk.edges]
            loc = {_rn(k, rename): v for k, v in blk.locals.items()}
            out.seq_blocks.append(SeqBlock(
                edges=edges,
                stmts=[_rename_stmt(s, rename) for s in blk.stmts],
                locals=loc))

        # Inline initial blocks
        for blk in child.initial_blocks:
            out.initial_blocks.append(InitialBlock(
                stmts=[_rename_stmt(s, rename) for s in blk.stmts]))

        # Inline always blocks
        for blk in child.always_blocks:
            out.always_blocks.append(AlwaysBlock(
                stmts=[_rename_stmt(s, rename) for s in blk.stmts]))

        # Inline formal properties
        for fp in child.formal_props:
            out.formal_props.append(FormalProperty(
                kind=fp.kind, clock=_rn(fp.clock, rename), edge=fp.edge,
                expr=_rename_expr(fp.expr, rename),
                name=prefix + fp.name))

    return out


# ── Topological sort ─────────────────────────────────────────────────

def topo_sort_comb(mod: IRModule) -> IRModule:
    """Return a copy of *mod* with assigns and comb_blocks topologically
    sorted so a single evaluation pass propagates all combinational values.

    Raises ``ValueError`` on combinational loops.
    """
    # Merge assigns and comb_blocks into a single list of nodes.
    # Each node is ('assign', ContAssign) or ('comb', CombBlock).
    nodes = []
    for a in mod.assigns:
        nodes.append(('assign', a))
    for b in mod.comb_blocks:
        nodes.append(('comb', b))

    if not nodes:
        return deepcopy(mod)

    # For each node, compute writes and reads.
    node_writes = []  # list[set[str]]
    node_reads = []   # list[set[str]]
    for kind, obj in nodes:
        if kind == 'assign':
            node_writes.append({obj.target})
            node_reads.append(_expr_reads(obj.value))
        else:
            w, r = set(), set()
            for s in obj.stmts:
                _stmt_writes_reads(s, w, r)
            node_writes.append(w)
            node_reads.append(r)

    # Map signal → node index that writes it (comb only)
    writer = {}
    for i, ws in enumerate(node_writes):
        for sig in ws:
            writer[sig] = i

    # Build adjacency list: edge from writer → reader
    n = len(nodes)
    adj = [[] for _ in range(n)]
    in_deg = [0] * n
    for j in range(n):
        for sig in node_reads[j]:
            i = writer.get(sig)
            if i is not None and i != j:
                adj[i].append(j)
                in_deg[j] += 1

    # Kahn's algorithm
    queue = deque(i for i in range(n) if in_deg[i] == 0)
    order = []
    while queue:
        u = queue.popleft()
        order.append(u)
        for v in adj[u]:
            in_deg[v] -= 1
            if in_deg[v] == 0:
                queue.append(v)

    if len(order) != n:
        raise ValueError('Combinational loop detected in comb assignments')

    # Rebuild assigns and comb_blocks in sorted order
    out = deepcopy(mod)
    out.assigns = []
    out.comb_blocks = []
    for i in order:
        kind, obj = nodes[i]
        if kind == 'assign':
            out.assigns.append(deepcopy(obj))
        else:
            out.comb_blocks.append(deepcopy(obj))

    return out


# ── Signal collection helpers ────────────────────────────────────────

def _expr_reads(node) -> set:
    """Return set of signal names read by an expression."""
    if isinstance(node, Sig):
        return {node.name}
    if isinstance(node, (Const, Param)):
        return set()
    if isinstance(node, BinOp):
        return _expr_reads(node.left) | _expr_reads(node.right)
    if isinstance(node, UnaryOp):
        return _expr_reads(node.operand)
    if isinstance(node, Compare):
        return _expr_reads(node.left) | _expr_reads(node.right)
    if isinstance(node, BoolOp):
        out = set()
        for v in node.values:
            out |= _expr_reads(v)
        return out
    if isinstance(node, Mux):
        return (_expr_reads(node.sel) | _expr_reads(node.true_val) |
                _expr_reads(node.false_val))
    if isinstance(node, Slice):
        r = _expr_reads(node.signal)
        if node.hi:
            r |= _expr_reads(node.hi)
        r |= _expr_reads(node.lo)
        return r
    if isinstance(node, Index):
        return _expr_reads(node.signal) | _expr_reads(node.idx)
    if isinstance(node, Concat):
        out = set()
        for p in node.parts:
            out |= _expr_reads(p)
        return out
    return set()


def _stmt_writes_reads(stmt, writes: set, reads: set):
    """Accumulate write targets and read signals from a statement."""
    if isinstance(stmt, Assign):
        writes.add(stmt.target)
        reads |= _expr_reads(stmt.value)
    elif isinstance(stmt, SliceAssign):
        writes.add(stmt.target)
        reads |= _expr_reads(stmt.hi) | _expr_reads(stmt.lo)
        reads |= _expr_reads(stmt.value)
    elif isinstance(stmt, MemWrite):
        writes.add(stmt.mem)
        reads |= _expr_reads(stmt.addr) | _expr_reads(stmt.data)
    elif isinstance(stmt, If):
        reads |= _expr_reads(stmt.cond)
        for s in stmt.then_body:
            _stmt_writes_reads(s, writes, reads)
        for s in (stmt.else_body or []):
            _stmt_writes_reads(s, writes, reads)
    elif isinstance(stmt, Case):
        reads |= _expr_reads(stmt.sel)
        for val, body in stmt.cases:
            reads |= _expr_reads(val)
            for s in body:
                _stmt_writes_reads(s, writes, reads)
        for s in (stmt.default or []):
            _stmt_writes_reads(s, writes, reads)


# ── Rename helpers ───────────────────────────────────────────────────

def _rn(name: str, rename: dict) -> str:
    return rename.get(name, name)


def _rename_expr(node, rename: dict):
    if isinstance(node, Const):
        return node
    if isinstance(node, Sig):
        return Sig(_rn(node.name, rename))
    if isinstance(node, Param):
        return Param(_rn(node.name, rename))
    if isinstance(node, BinOp):
        return BinOp(node.op, _rename_expr(node.left, rename),
                     _rename_expr(node.right, rename))
    if isinstance(node, UnaryOp):
        return UnaryOp(node.op, _rename_expr(node.operand, rename))
    if isinstance(node, Compare):
        return Compare(node.op, _rename_expr(node.left, rename),
                       _rename_expr(node.right, rename))
    if isinstance(node, BoolOp):
        return BoolOp(node.op, [_rename_expr(v, rename) for v in node.values])
    if isinstance(node, Mux):
        return Mux(_rename_expr(node.sel, rename),
                   _rename_expr(node.true_val, rename),
                   _rename_expr(node.false_val, rename))
    if isinstance(node, Slice):
        return Slice(_rename_expr(node.signal, rename),
                     _rename_expr(node.hi, rename) if node.hi else None,
                     _rename_expr(node.lo, rename))
    if isinstance(node, Index):
        return Index(_rename_expr(node.signal, rename),
                     _rename_expr(node.idx, rename))
    if isinstance(node, Concat):
        return Concat([_rename_expr(p, rename) for p in node.parts])
    return node


def _rename_stmt(stmt, rename: dict):
    if isinstance(stmt, Assign):
        return Assign(_rn(stmt.target, rename),
                      _rename_expr(stmt.value, rename), stmt.blocking)
    if isinstance(stmt, SliceAssign):
        return SliceAssign(_rn(stmt.target, rename),
                           _rename_expr(stmt.hi, rename),
                           _rename_expr(stmt.lo, rename),
                           _rename_expr(stmt.value, rename), stmt.blocking)
    if isinstance(stmt, MemWrite):
        return MemWrite(_rn(stmt.mem, rename), _rename_expr(stmt.addr, rename),
                        _rename_expr(stmt.data, rename), stmt.blocking)
    if isinstance(stmt, If):
        return If(_rename_expr(stmt.cond, rename),
                  [_rename_stmt(s, rename) for s in stmt.then_body],
                  [_rename_stmt(s, rename) for s in stmt.else_body] if stmt.else_body else [])
    if isinstance(stmt, Case):
        cases = [(_rename_expr(v, rename), [_rename_stmt(s, rename) for s in body])
                 for v, body in stmt.cases]
        default = [_rename_stmt(s, rename) for s in stmt.default] if stmt.default else None
        return Case(_rename_expr(stmt.sel, rename), cases, default)
    return stmt
