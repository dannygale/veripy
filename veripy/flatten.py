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

    def _resolve_dim(val, params):
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            if val in params:
                return params[val]
            try:
                return int(eval(val, {"__builtins__": {}}, params))
            except Exception:
                return val
        return val

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
        for blk in child.comb_blocks:
            for name in blk.locals:
                if name not in rename:
                    rename[name] = prefix + name
        for blk in child.seq_blocks:
            for name in blk.locals:
                if name not in rename:
                    rename[name] = prefix + name

        # Add child internal signals (non-port) to parent
        child_port_names = {p.name for p in child.ports}
        for r in child.regs:
            if r.name not in child_port_names:
                out.regs.append(RegDecl(prefix + r.name, _resolve_dim(r.width, child_params)))
        for w in child.wires:
            if w.name not in child_port_names:
                out.wires.append(WireDecl(prefix + w.name, _resolve_dim(w.width, child_params)))

        for m in child.mems:
            depth = _resolve_dim(m.depth, child_params)
            width = _resolve_dim(m.width, child_params)
            if isinstance(m, TrueDualPortMemDecl):
                out.mems.append(TrueDualPortMemDecl(
                    prefix + m.name, depth, width, style=m.style,
                    clka=_rn(m.clka, rename), wea=_rn(m.wea, rename),
                    addra=_rn(m.addra, rename), dina=_rn(m.dina, rename),
                    douta=_rn(m.douta, rename),
                    clkb=_rn(m.clkb, rename), web=_rn(m.web, rename),
                    addrb=_rn(m.addrb, rename), dinb=_rn(m.dinb, rename),
                    doutb=_rn(m.doutb, rename)))
            elif isinstance(m, DualPortMemDecl):
                out.mems.append(DualPortMemDecl(
                    prefix + m.name, depth, width, style=m.style,
                    clock=_rn(m.clock, rename), we=_rn(m.we, rename),
                    waddr=_rn(m.waddr, rename), wdata=_rn(m.wdata, rename),
                    raddr=_rn(m.raddr, rename), rdata=_rn(m.rdata, rename)))
            else:
                out.mems.append(MemDecl(prefix + m.name, depth, width,
                                        style=m.style))

        # Inline assigns
        for a in child.assigns:
            out.assigns.append(ContAssign(
                _rn(a.target, rename), _rename_expr(a.value, rename, child_params)))

        # Inline comb blocks
        for blk in child.comb_blocks:
            loc = {_rn(k, rename): v for k, v in blk.locals.items()}
            out.comb_blocks.append(CombBlock(
                stmts=[_rename_stmt(s, rename, child_params) for s in blk.stmts],
                locals=loc))

        # Inline seq blocks
        for blk in child.seq_blocks:
            edges = [(kind, _rn(sig, rename)) for kind, sig in blk.edges]
            loc = {_rn(k, rename): v for k, v in blk.locals.items()}
            out.seq_blocks.append(SeqBlock(
                edges=edges,
                stmts=[_rename_stmt(s, rename, child_params) for s in blk.stmts],
                locals=loc))

        # Inline initial blocks
        for blk in child.initial_blocks:
            out.initial_blocks.append(InitialBlock(
                stmts=[_rename_stmt(s, rename, child_params) for s in blk.stmts]))

        # Inline always blocks
        for blk in child.always_blocks:
            out.always_blocks.append(AlwaysBlock(
                stmts=[_rename_stmt(s, rename, child_params) for s in blk.stmts]))

        # Inline formal properties
        for fp in child.formal_props:
            out.formal_props.append(FormalProperty(
                kind=fp.kind, clock=_rn(fp.clock, rename), edge=fp.edge,
                expr=_rename_expr(fp.expr, rename, child_params),
                name=prefix + fp.name))

    # Resolve remaining Param nodes in parent's own blocks
    identity = {}  # no signal renames needed
    for i, a in enumerate(out.assigns):
        out.assigns[i] = ContAssign(a.target, _rename_expr(a.value, identity, params))
    for i, blk in enumerate(out.comb_blocks):
        out.comb_blocks[i] = CombBlock(
            stmts=[_rename_stmt(s, identity, params) for s in blk.stmts],
            locals=blk.locals)
    for i, blk in enumerate(out.seq_blocks):
        out.seq_blocks[i] = SeqBlock(
            edges=blk.edges,
            stmts=[_rename_stmt(s, identity, params) for s in blk.stmts],
            locals=blk.locals)

    # Resolve parametric widths on parent's own signals and ports
    _resolve_dim_p = lambda v: _resolve_dim(v, params) if isinstance(v, str) else v
    out.ports = [Port(p.name, p.direction, _resolve_dim_p(p.width)) for p in out.ports]
    out.regs = [RegDecl(r.name, _resolve_dim_p(r.width)) for r in out.regs]
    out.wires = [WireDecl(w.name, _resolve_dim_p(w.width)) for w in out.wires]

    # Post-pass: reclassify registers that are only driven by comb blocks.
    # Pipeline stage wiring can create submodule port signals as regs when
    # they should be wires (e.g. lsu.wb_mem_data = mem_wb.mem_data).
    seq_writes: set = set()
    for blk in out.seq_blocks:
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, seq_writes, set())
    comb_writes: set = set()
    for blk in out.comb_blocks:
        for stmt in blk.stmts:
            _stmt_writes_reads(stmt, comb_writes, set())
    for a in out.assigns:
        _stmt_writes_reads(a, comb_writes, set())
    reg_names = {r.name for r in out.regs}
    comb_only_regs = (comb_writes & reg_names) - seq_writes
    if comb_only_regs:
        kept_regs = []
        for r in out.regs:
            if r.name in comb_only_regs:
                out.wires.append(WireDecl(r.name, r.width))
            else:
                kept_regs.append(r)
        out.regs = kept_regs

    return out

def topo_sort_comb(mod: IRModule) -> IRModule:
    """Return a copy of *mod* with assigns and comb_blocks topologically
    sorted so a single evaluation pass propagates all combinational values.

    Raises ``ValueError`` on combinational loops.
    """
    # Merge assigns and comb_blocks into a single list of nodes.
    # Each node is ('assign', ContAssign) or ('comb', CombBlock).
    # Multi-statement comb blocks are exploded into per-statement nodes
    # so the topo sort can order statements independently and break false
    # cycles caused by a single block both writing and reading across a
    # dependency boundary.
    nodes = []
    node_block_id = []  # original block index for intra-block edges
    bid = 0
    for a in mod.assigns:
        nodes.append(('assign', a))
        node_block_id.append(bid); bid += 1
    for b in mod.comb_blocks:
        if len(b.stmts) <= 1:
            nodes.append(('comb', b))
            node_block_id.append(bid)
        else:
            for stmt in b.stmts:
                nodes.append(('comb', CombBlock(stmts=[stmt], locals=b.locals)))
                node_block_id.append(bid)
        bid += 1

    if not nodes:
        return deepcopy(mod)

    # Collect all comb-block-local variable names — these use blocking
    # assignment semantics and must not participate in the inter-node
    # dependency graph.
    all_locals: set = set()
    for b in mod.comb_blocks:
        all_locals |= set(b.locals)

    # For each node, compute writes and reads (excluding locals for
    # inter-block deps, full sets kept for intra-block local deps).
    node_writes = []  # list[set[str]]  — inter-block (locals excluded)
    node_reads = []   # list[set[str]]
    node_writes_full = []  # list[set[str]]  — includes locals
    node_reads_full = []
    for kind, obj in nodes:
        if kind == 'assign':
            w = {obj.target}
            r = _expr_reads(obj.value)
            node_writes.append(w); node_reads.append(r)
            node_writes_full.append(w); node_reads_full.append(r)
        else:
            w, r = set(), set()
            for s in obj.stmts:
                _stmt_writes_reads(s, w, r)
            node_writes.append(w - all_locals)
            node_reads.append(r - all_locals)
            node_writes_full.append(w)
            node_reads_full.append(r)

    # Map signal → node index that writes it (comb only)
    writer = {}
    for i, ws in enumerate(node_writes):
        for sig in ws:
            writer[sig] = i

    # Build adjacency list: edge from writer → reader (inter-block)
    n = len(nodes)
    adj = [[] for _ in range(n)]
    in_deg = [0] * n
    edge_set = set()
    for j in range(n):
        for sig in node_reads[j]:
            i = writer.get(sig)
            if i is not None and i != j:
                edge_set.add((i, j))
                adj[i].append(j)
                in_deg[j] += 1

    # Add intra-block edges for local variable dependencies.
    # Exploded statements from the same original comb block must
    # preserve data-flow order through locals.  Process in original
    # statement order so each read sees the most-recent writer.
    from collections import defaultdict
    block_nodes = defaultdict(list)
    for i in range(n):
        block_nodes[node_block_id[i]].append(i)
    for idxs in block_nodes.values():
        if len(idxs) <= 1:
            continue
        cur_writer = {}
        for i in idxs:
            # Local variable read → writer edge
            for sig in node_reads_full[i]:
                if sig not in all_locals:
                    continue
                src = cur_writer.get(sig)
                if src is not None and src != i and (src, i) not in edge_set:
                    edge_set.add((src, i))
                    adj[src].append(i)
                    in_deg[i] += 1
            # Same-signal write ordering: if an earlier statement wrote
            # the same signal, the earlier one must execute first (it's
            # a default that the later statement overrides).
            for sig in node_writes_full[i]:
                src = cur_writer.get(sig)
                if src is not None and src != i and (src, i) not in edge_set:
                    edge_set.add((src, i))
                    adj[src].append(i)
                    in_deg[i] += 1
                cur_writer[sig] = i

    # Kahn's algorithm — level-aware with consumer grouping.
    # Within each topo level, sort nodes so that nodes feeding the same
    # downstream consumer are adjacent.  This keeps recently-written
    # values in L1 cache when the consumer executes.
    level = [i for i in range(n) if in_deg[i] == 0]
    order = []
    while level:
        level.sort(key=lambda u: tuple(sorted(adj[u])) if adj[u] else (n,))
        next_level = []
        for u in level:
            order.append(u)
            for v in adj[u]:
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    next_level.append(v)
        level = next_level

    if len(order) != n:
        remaining = set(range(n)) - set(order)
        loop_sigs = _find_loop_signals(nodes, adj, remaining)
        raise ValueError(
            f'Combinational loop detected in comb assignments. '
            f'Signals in loop: {sorted(loop_sigs)}'
        )

    # Rebuild assigns and comb_blocks in sorted order.
    # All nodes go into comb_blocks to preserve interleaved ordering;
    # ContAssigns become single-statement CombBlocks.
    out = deepcopy(mod)
    out.assigns = []
    out.comb_blocks = []
    for i in order:
        kind, obj = nodes[i]
        if kind == 'assign':
            out.comb_blocks.append(CombBlock(
                stmts=[Assign(obj.target, deepcopy(obj.value))], locals={}))
        else:
            out.comb_blocks.append(deepcopy(obj))

    return out


def _find_loop_signals(nodes, adj, remaining: set) -> set:
    """Return signal names written by nodes involved in a combinational cycle.

    Uses DFS to find a cycle in the subgraph of *remaining* nodes, then
    collects all signal names written by nodes on that cycle.
    """
    sub_adj = {u: [v for v in adj[u] if v in remaining] for u in remaining}
    visited, on_stack, stack_list = set(), set(), []
    cycle_nodes: set = set()

    def _dfs(u) -> bool:
        visited.add(u); on_stack.add(u); stack_list.append(u)
        for v in sub_adj[u]:
            if v not in visited:
                if _dfs(v):
                    return True
            elif v in on_stack:
                # Found back-edge u→v; collect all nodes from v to u on stack
                idx = stack_list.index(v)
                cycle_nodes.update(stack_list[idx:])
                return True
        stack_list.pop(); on_stack.discard(u)
        return False

    for start in remaining:
        if start not in visited:
            if _dfs(start):
                break

    sigs: set = set()
    for i in cycle_nodes:
        kind, obj = nodes[i]
        if kind == 'assign':
            sigs.add(obj.target)
        else:
            for s in obj.stmts:
                w: set = set()
                _stmt_writes_reads(s, w, set())
                sigs |= w
    return sigs


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


def _rename_expr(node, rename: dict, resolved_params: dict | None = None):
    if isinstance(node, Const):
        return node
    if isinstance(node, Sig):
        return Sig(_rn(node.name, rename))
    if isinstance(node, Param):
        if resolved_params and node.name in resolved_params:
            v = resolved_params[node.name]
            if isinstance(v, int):
                return Const(v)
        return Param(_rn(node.name, rename))
    if isinstance(node, BinOp):
        return BinOp(node.op, _rename_expr(node.left, rename, resolved_params),
                     _rename_expr(node.right, rename, resolved_params))
    if isinstance(node, UnaryOp):
        return UnaryOp(node.op, _rename_expr(node.operand, rename, resolved_params))
    if isinstance(node, Compare):
        return Compare(node.op, _rename_expr(node.left, rename, resolved_params),
                       _rename_expr(node.right, rename, resolved_params))
    if isinstance(node, BoolOp):
        return BoolOp(node.op, [_rename_expr(v, rename, resolved_params) for v in node.values])
    if isinstance(node, Mux):
        return Mux(_rename_expr(node.sel, rename, resolved_params),
                   _rename_expr(node.true_val, rename, resolved_params),
                   _rename_expr(node.false_val, rename, resolved_params))
    if isinstance(node, Slice):
        return Slice(_rename_expr(node.signal, rename, resolved_params),
                     _rename_expr(node.hi, rename, resolved_params) if node.hi else None,
                     _rename_expr(node.lo, rename, resolved_params))
    if isinstance(node, Index):
        return Index(_rename_expr(node.signal, rename, resolved_params),
                     _rename_expr(node.idx, rename, resolved_params))
    if isinstance(node, Concat):
        return Concat([_rename_expr(p, rename) for p in node.parts])
    return node


def _rename_stmt(stmt, rename: dict, resolved_params: dict | None = None):
    _re = lambda n: _rename_expr(n, rename, resolved_params)
    _rs = lambda s: _rename_stmt(s, rename, resolved_params)
    if isinstance(stmt, Assign):
        return Assign(_rn(stmt.target, rename), _re(stmt.value), stmt.blocking)
    if isinstance(stmt, SliceAssign):
        return SliceAssign(_rn(stmt.target, rename),
                           _re(stmt.hi), _re(stmt.lo), _re(stmt.value), stmt.blocking)
    if isinstance(stmt, MemWrite):
        return MemWrite(_rn(stmt.mem, rename), _re(stmt.addr), _re(stmt.data), stmt.blocking)
    if isinstance(stmt, If):
        return If(_re(stmt.cond),
                  [_rs(s) for s in stmt.then_body],
                  [_rs(s) for s in stmt.else_body] if stmt.else_body else [])
    if isinstance(stmt, Case):
        cases = [(_re(v), [_rs(s) for s in body]) for v, body in stmt.cases]
        default = [_rs(s) for s in stmt.default] if stmt.default else None
        return Case(_re(stmt.sel), cases, default)
    return stmt
