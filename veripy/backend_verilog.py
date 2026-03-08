"""Verilog backend: IRModule → Verilog string.

Pure pattern matching on IR nodes — no Python AST, no name resolution.
"""

from .ir import (
    Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat, Clz, Ctz, Popcount, Sext,
    Assign, SliceAssign, If, Case, MemWrite, Delay, Display, Finish,
    Repeat, ForLoop, Disable,
    ContAssign, CombBlock, SeqBlock, InitialBlock, AlwaysBlock,
    DualPortMemDecl, TrueDualPortMemDecl, FormalProperty,
    SeqBool, SeqConcat, SeqRepeat, SeqAnd, SeqOr, SeqNot,
    SeqImplication, SeqWithin, SeqEventually, TemporalProperty,
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
    _emit_dual_port_mems(ir, lines)
    _emit_initial_blocks(ir, lines)
    _emit_always_blocks(ir, lines)
    _emit_formal_props(ir, lines)
    _emit_temporal_props(ir, lines)
    _emit_builtin_funcs(ir, lines)
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
        attr = f'(* ram_style = "{m.style}" *) ' if m.style else ''
        lines.append(f'    {attr}reg {w}{m.name} [0:{_sub1(d)}];')

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


# ── Dual-port memory blocks ─────────────────────────────────────────

def _emit_dual_port_mems(ir, lines):
    for m in ir.mems:
        if isinstance(m, DualPortMemDecl):
            lines.append('')
            lines.append(f'    always @(posedge {m.clock}) begin')
            lines.append(f'        if ({m.we})')
            lines.append(f'            {m.name}[{m.waddr}] <= {m.wdata};')
            lines.append(f'        {m.rdata} <= {m.name}[{m.raddr}];')
            lines.append('    end')
        elif isinstance(m, TrueDualPortMemDecl):
            lines.append('')
            lines.append(f'    always @(posedge {m.clka}) begin')
            lines.append(f'        if ({m.wea})')
            lines.append(f'            {m.name}[{m.addra}] <= {m.dina};')
            lines.append(f'        {m.douta} <= {m.name}[{m.addra}];')
            lines.append('    end')
            lines.append(f'    always @(posedge {m.clkb}) begin')
            lines.append(f'        if ({m.web})')
            lines.append(f'            {m.name}[{m.addrb}] <= {m.dinb};')
            lines.append(f'        {m.doutb} <= {m.name}[{m.addrb}];')
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

    elif isinstance(stmt, Delay):
        lines.append(f'{pad}#{_expr(stmt.value)};')

    elif isinstance(stmt, Display):
        args = ', '.join(_expr(a) for a in stmt.args)
        lines.append(f'{pad}$display("{stmt.fmt}", {args});')

    elif isinstance(stmt, Finish):
        lines.append(f'{pad}$finish;')

    elif isinstance(stmt, Repeat):
        if stmt.label:
            lines.append(f'{pad}begin : {stmt.label}')
            lines.append(f'{pad}    repeat ({_expr(stmt.count)}) begin')
            for s in stmt.body:
                _emit_stmt(s, lines, indent + 2)
            lines.append(f'{pad}    end')
            lines.append(f'{pad}end')
        else:
            lines.append(f'{pad}repeat ({_expr(stmt.count)}) begin')
            for s in stmt.body:
                _emit_stmt(s, lines, indent + 1)
            lines.append(f'{pad}end')

    elif isinstance(stmt, Disable):
        lines.append(f'{pad}disable {stmt.label};')

    elif isinstance(stmt, ForLoop):
        v = stmt.var
        hdr = f'for ({v} = {_expr(stmt.start)}; {v} < {_expr(stmt.stop)}; {v} = {v} + 1) begin'
        if stmt.label:
            lines.append(f'{pad}begin : {stmt.label}')
            lines.append(f'{pad}    {hdr}')
            for s in stmt.body:
                _emit_stmt(s, lines, indent + 2)
            lines.append(f'{pad}    end')
            lines.append(f'{pad}end')
        else:
            lines.append(f'{pad}{hdr}')
            for s in stmt.body:
                _emit_stmt(s, lines, indent + 1)
            lines.append(f'{pad}end')


# ── Initial blocks ───────────────────────────────────────────────────

def _emit_initial_blocks(ir, lines):
    for blk in ir.initial_blocks:
        lines.append('')
        lines.append('    initial begin')
        for s in blk.stmts:
            _emit_stmt(s, lines, indent=2)
        lines.append('    end')


# ── Always blocks (free-running) ─────────────────────────────────────

def _emit_always_blocks(ir, lines):
    for blk in ir.always_blocks:
        lines.append('')
        lines.append('    always begin')
        for s in blk.stmts:
            _emit_stmt(s, lines, indent=2)
        lines.append('    end')


# ── Formal properties ────────────────────────────────────────────────

def _emit_formal_props(ir, lines):
    if not ir.formal_props:
        return
    lines.append('')
    lines.append('`ifdef FORMAL')
    for prop in ir.formal_props:
        kw = {'assert': 'assert', 'cover': 'cover', 'assume': 'assume'}[prop.kind]
        lines.append(f'    always @({prop.edge} {prop.clock}) begin')
        lines.append(f'        {kw}({_expr(prop.expr)});  // {prop.name}')
        lines.append(f'    end')
    lines.append('`endif')


def _seq_expr(node) -> str:
    """Emit an SVA sequence expression as a Verilog string."""
    if isinstance(node, SeqBool):
        return _expr(node.expr)
    if isinstance(node, SeqConcat):
        lo, hi = node.lo, node.hi
        if lo == hi:
            delay = f'##{lo}'
        elif hi == -1:
            delay = f'##[{lo}:$]'
        else:
            delay = f'##[{lo}:{hi}]'
        return f'({_seq_expr(node.left)} {delay} {_seq_expr(node.right)})'
    if isinstance(node, SeqRepeat):
        lo, hi = node.lo, node.hi
        if lo == 0 and hi == -1:
            rep = '[*]'
        elif lo == 1 and hi == -1:
            rep = '[+]'
        elif lo == hi:
            rep = f'[*{lo}]'
        elif hi == -1:
            rep = f'[*{lo}:$]'
        else:
            rep = f'[*{lo}:{hi}]'
        return f'({_seq_expr(node.seq)}{rep})'
    if isinstance(node, SeqAnd):
        return f'({_seq_expr(node.left)} and {_seq_expr(node.right)})'
    if isinstance(node, SeqOr):
        return f'({_seq_expr(node.left)} or {_seq_expr(node.right)})'
    if isinstance(node, SeqNot):
        return f'(not {_seq_expr(node.seq)})'
    if isinstance(node, SeqImplication):
        op = '|->' if node.overlapping else '|=>'
        return f'({_seq_expr(node.antecedent)} {op} {_seq_expr(node.consequent)})'
    if isinstance(node, SeqWithin):
        return f'({_seq_expr(node.inner)} within {_seq_expr(node.outer)})'
    if isinstance(node, SeqEventually):
        return f'(s_eventually {_seq_expr(node.seq)})'
    raise TypeError(f"Unknown SeqExpr node: {type(node).__name__}")


def _emit_temporal_props(ir, lines):
    if not ir.temporal_props:
        return
    lines.append('')
    lines.append('`ifdef FORMAL')
    for prop in ir.temporal_props:
        kw = {'assert': 'assert property', 'cover': 'cover property',
              'assume': 'assume property'}[prop.kind]
        seq_str = _seq_expr(prop.seq)
        lines.append(f'    property {prop.name}_prop;')
        lines.append(f'        @({prop.edge} {prop.clock}) {seq_str};')
        lines.append(f'    endproperty')
        lines.append(f'    {kw} ({prop.name}_prop);  // {prop.name}')
    lines.append('`endif')


# ── Built-in function emission ───────────────────────────────────────

def _scan_ir_exprs(ir):
    """Collect all Clz/Ctz/Popcount nodes used in the IR."""
    needed = set()
    def _walk_expr(e):
        if isinstance(e, (Clz, Ctz, Popcount)):
            needed.add((type(e).__name__.lower(), e.width))
            _walk_expr(e.operand)
        elif isinstance(e, Sext):
            _walk_expr(e.operand)
        elif isinstance(e, BinOp):
            _walk_expr(e.left); _walk_expr(e.right)
        elif isinstance(e, UnaryOp):
            _walk_expr(e.operand)
        elif isinstance(e, Compare):
            _walk_expr(e.left); _walk_expr(e.right)
        elif isinstance(e, BoolOp):
            for v in e.values: _walk_expr(v)
        elif isinstance(e, Mux):
            _walk_expr(e.sel); _walk_expr(e.true_val); _walk_expr(e.false_val)
        elif isinstance(e, Slice):
            _walk_expr(e.signal)
        elif isinstance(e, Index):
            _walk_expr(e.signal); _walk_expr(e.idx)
        elif isinstance(e, Concat):
            for p in e.parts: _walk_expr(p)
    def _walk_stmt(s):
        if isinstance(s, Assign):
            _walk_expr(s.value)
        elif isinstance(s, SliceAssign):
            _walk_expr(s.value)
        elif isinstance(s, If):
            _walk_expr(s.cond)
            for st in s.then_body: _walk_stmt(st)
            for st in s.else_body: _walk_stmt(st)
        elif isinstance(s, Case):
            _walk_expr(s.sel)
            for _, stmts in s.cases:
                for st in stmts: _walk_stmt(st)
            if s.default:
                for st in s.default: _walk_stmt(st)
    for blk in ir.comb_blocks:
        for s in blk.stmts: _walk_stmt(s)
    for blk in ir.seq_blocks:
        for s in blk.stmts: _walk_stmt(s)
    for ca in ir.assigns:
        _walk_expr(ca.value)
    return needed

def _emit_builtin_funcs(ir, lines):
    needed = _scan_ir_exprs(ir)
    if not needed:
        return
    rw = lambda w: (w + 1).bit_length()  # result width
    for kind, w in sorted(needed):
        lines.append('')
        if kind == 'clz':
            lines.append(f'    function [{rw(w)-1}:0] _veripy_clz{w};')
            lines.append(f'        input [{w-1}:0] val;')
            lines.append(f'        integer i;')
            lines.append(f'        begin')
            lines.append(f'            _veripy_clz{w} = {w};')
            lines.append(f'            for (i = 0; i < {w}; i = i + 1)')
            lines.append(f'                if (val[i]) _veripy_clz{w} = {w-1} - i;')
            lines.append(f'        end')
            lines.append(f'    endfunction')
        elif kind == 'ctz':
            lines.append(f'    function [{rw(w)-1}:0] _veripy_ctz{w};')
            lines.append(f'        input [{w-1}:0] val;')
            lines.append(f'        integer i;')
            lines.append(f'        begin')
            lines.append(f'            _veripy_ctz{w} = {w};')
            lines.append(f'            for (i = {w-1}; i >= 0; i = i - 1)')
            lines.append(f'                if (val[i]) _veripy_ctz{w} = i;')
            lines.append(f'        end')
            lines.append(f'    endfunction')
        elif kind == 'popcount':
            lines.append(f'    function [{rw(w)-1}:0] _veripy_popcount{w};')
            lines.append(f'        input [{w-1}:0] val;')
            lines.append(f'        integer i;')
            lines.append(f'        begin')
            lines.append(f'            _veripy_popcount{w} = 0;')
            lines.append(f'            for (i = 0; i < {w}; i = i + 1)')
            lines.append(f'                if (val[i]) _veripy_popcount{w} = _veripy_popcount{w} + 1;')
            lines.append(f'        end')
            lines.append(f'    endfunction')


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

    if isinstance(node, Clz):
        return f'_veripy_clz{node.width}({_expr(node.operand)})'

    if isinstance(node, Ctz):
        return f'_veripy_ctz{node.width}({_expr(node.operand)})'

    if isinstance(node, Popcount):
        return f'_veripy_popcount{node.width}({_expr(node.operand)})'

    if isinstance(node, Sext):
        src = _expr(node.operand)
        sw, dw = node.src_width, node.dst_width
        sign_bit = f'{src}[{sw - 1}]'
        mask = (1 << dw) - (1 << sw)
        return f'({sign_bit} ? ({src} | {dw}\'d{mask}) : {src})'

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
