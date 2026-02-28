"""Verilog backend: IRModule → Verilog string.

Pure pattern matching on IR nodes — no Python AST, no name resolution.
"""

from .ir import (
    Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock,
    IRModule,
)


def emit_verilog(ir: IRModule) -> str:
    lines = []
    _emit_header(ir, lines)
    _emit_internals(ir, lines)
    _emit_instances(ir, lines)
    _emit_comb_blocks(ir, lines)
    _emit_assigns(ir, lines)
    _emit_seq_blocks(ir, lines)
    lines.append('')
    lines.append('endmodule')
    return '\n'.join(lines)


# ── Header ───────────────────────────────────────────────────────────

def _emit_header(ir, lines):
    # Module declaration with parameters
    if ir.params:
        lines.append(f'module {ir.name} #(')
        param_lines = []
        for pname, pval in ir.params.items():
            param_lines.append(f'    parameter {pname} = {pval}')
        lines.append(',\n'.join(param_lines))
        lines.append(') (')
    else:
        lines.append(f'module {ir.name} (')

    # Ports — inputs first, then outputs (each group sorted)
    port_lines = []
    for p in sorted(ir.ports, key=lambda p: (0 if p.direction == 'input' else 1, p.name)):
        w = _width_decl(p.width)
        reg = ' reg' if p.is_reg else ''
        port_lines.append(f'    {p.direction}{reg} {w}{p.name}')
    lines.append(',\n'.join(port_lines))
    lines.append(');')


# ── Internal declarations ────────────────────────────────────────────

def _emit_internals(ir, lines):
    has_any = ir.regs or ir.wires or ir.mems
    lines.append('')  # blank line after header

    for r in ir.regs:
        lines.append(f'    reg {_width_decl(r.width)}{r.name};')

    for w in ir.wires:
        lines.append(f'    wire {_width_decl(w.width)}{w.name};')

    for m in ir.mems:
        w = _width_decl(m.width)
        d = m.depth
        lines.append(f'    reg {w}{m.name} [0:{_sub1(d)}];')

    if ir.mems:
        lines.append('    integer _i;')
        lines.append('    initial begin')
        for m in ir.mems:
            d = m.depth
            lines.append(f'        for (_i = 0; _i < {d}; _i = _i + 1)')
            lines.append(f'            {m.name}[_i] = 0;')
        lines.append('    end')


# ── Sub-module instances ─────────────────────────────────────────────

def _emit_instances(ir, lines):
    for inst in ir.instances:
        lines.append('')
        # Parameters
        if inst.params:
            param_str = ', '.join(f'.{k}({v})' for k, v in inst.params.items())
            lines.append(f'    {inst.mod_type} #({param_str}) {inst.inst_name} (')
        else:
            lines.append(f'    {inst.mod_type} {inst.inst_name} (')
        # Ports
        port_lines = []
        for pname, wire in inst.ports:
            port_lines.append(f'        .{pname}({wire})')
        lines.append(',\n'.join(port_lines))
        lines.append('    );')


# ── Continuous assigns ───────────────────────────────────────────────

def _emit_assigns(ir, lines):
    if ir.assigns:
        lines.append('')
    for a in ir.assigns:
        lines.append(f'    assign {a.target} = {_expr(a.value)};')


# ── Combinational blocks ────────────────────────────────────────────

def _emit_comb_blocks(ir, lines):
    for blk in ir.comb_blocks:
        lines.append('')
        # Local reg declarations for always @(*) blocks
        for name, width in blk.locals.items():
            lines.append(f'    reg {_width_decl(width)}{name};')
        lines.append('    always @(*) begin')
        for s in blk.stmts:
            _emit_stmt(s, lines, indent=2)
        lines.append('    end')


# ── Sequential blocks ───────────────────────────────────────────────

def _emit_seq_blocks(ir, lines):
    for blk in ir.seq_blocks:
        lines.append('')
        for name, width in blk.locals.items():
            lines.append(f'    reg {_width_decl(width)}{name};')
        sens = ' or '.join(f'{kind} {sig}' for kind, sig in blk.edges)
        lines.append(f'    always @({sens}) begin')
        for s in blk.stmts:
            _emit_stmt(s, lines, indent=2)
        lines.append('    end')


# ── Statement emission ───────────────────────────────────────────────

def _emit_stmt(stmt, lines, indent=2):
    pad = '    ' * indent

    if isinstance(stmt, Assign):
        op = '=' if stmt.blocking else '<='
        lines.append(f'{pad}{stmt.target} {op} {_expr(stmt.value)};')

    elif isinstance(stmt, SliceAssign):
        op = '=' if stmt.blocking else '<='
        hi, lo = _expr(stmt.hi), _expr(stmt.lo)
        if hi == lo:
            lines.append(f'{pad}{stmt.target}[{hi}] {op} {_expr(stmt.value)};')
        else:
            lines.append(f'{pad}{stmt.target}[{hi}:{lo}] {op} {_expr(stmt.value)};')

    elif isinstance(stmt, MemWrite):
        op = '=' if stmt.blocking else '<='
        lines.append(f'{pad}{stmt.mem}[{_expr(stmt.addr)}] {op} {_expr(stmt.data)};')

    elif isinstance(stmt, If):
        lines.append(f'{pad}if ({_expr(stmt.cond)}) begin')
        for s in stmt.then_body:
            _emit_stmt(s, lines, indent + 1)
        if stmt.else_body:
            # Check for else-if chain
            if (len(stmt.else_body) == 1 and isinstance(stmt.else_body[0], If)):
                lines.append(f'{pad}end else')
                _emit_stmt(stmt.else_body[0], lines, indent)
            else:
                lines.append(f'{pad}end else begin')
                for s in stmt.else_body:
                    _emit_stmt(s, lines, indent + 1)
                lines.append(f'{pad}end')
        else:
            lines.append(f'{pad}end')

    elif isinstance(stmt, Case):
        lines.append(f'{pad}case ({_expr(stmt.sel)})')
        for val, body in stmt.cases:
            lines.append(f'{pad}    {_expr(val)}: begin')
            for s in body:
                _emit_stmt(s, lines, indent + 2)
            lines.append(f'{pad}    end')
        if stmt.default:
            lines.append(f'{pad}    default: begin')
            for s in stmt.default:
                _emit_stmt(s, lines, indent + 2)
            lines.append(f'{pad}    end')
        lines.append(f'{pad}endcase')


# ── Expression emission ──────────────────────────────────────────────

def _expr(node) -> str:
    if isinstance(node, Const):
        v = node.value
        if isinstance(v, int) and v < 0:
            return f'(-{abs(v)})'
        return str(v)

    if isinstance(node, Param):
        return node.name

    if isinstance(node, Sig):
        return node.name

    if isinstance(node, BinOp):
        return f'({_expr(node.left)} {node.op} {_expr(node.right)})'

    if isinstance(node, UnaryOp):
        return f'{node.op}{_expr(node.operand)}'

    if isinstance(node, Compare):
        return f'({_expr(node.left)} {node.op} {_expr(node.right)})'

    if isinstance(node, BoolOp):
        parts = []
        for v in node.values:
            s = _expr(v)
            if isinstance(v, BoolOp):
                s = f'({s})'
            parts.append(s)
        return ' {op} '.format(op=node.op).join(parts)

    if isinstance(node, Mux):
        return f'(({_expr(node.sel)}) ? {_expr(node.true_val)} : {_expr(node.false_val)})'

    if isinstance(node, Slice):
        if node.hi is None:
            return f'{_expr(node.signal)}[{_expr(node.lo)}]'
        return f'{_expr(node.signal)}[{_expr(node.hi)}:{_expr(node.lo)}]'

    if isinstance(node, Index):
        return f'{_expr(node.signal)}[{_expr(node.idx)}]'

    if isinstance(node, Concat):
        parts = [_expr(e) for e in node.parts]
        return '{' + ', '.join(parts) + '}'

    raise ValueError(f'Unknown IR expr: {node}')


# ── Helpers ──────────────────────────────────────────────────────────

def _width_decl(w) -> str:
    """Format width for declarations: '' for 1-bit, '[N-1:0] ' for wider."""
    if isinstance(w, str):
        return f'[{w}-1:0] '
    if w == 1:
        return ''
    return f'[{w - 1}:0] '


def _sub1(v):
    """v - 1, handling both int and param name string."""
    if isinstance(v, str):
        return f'{v}-1'
    return v - 1
