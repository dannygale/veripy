"""Flatten sub-module instances into a single-level IRModule.

Used by the WGSL backend which cannot emit Verilog-style module instantiation.
"""

from copy import deepcopy
from .ir import (
    Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock, IRModule,
    Port, WireDecl, RegDecl, MemDecl,
)


def flatten_ir(parent: IRModule, registry: dict[str, IRModule],
               params: dict | None = None) -> IRModule:
    """Return a new IRModule with all instances inlined.

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
            out.mems.append(MemDecl(prefix + m.name, m.depth, m.width))

        # Inline comb blocks: assigns and comb_blocks
        for a in child.assigns:
            out.assigns.append(ContAssign(
                _rn(a.target, rename), _rename_expr(a.value, rename)))
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

    return out


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
