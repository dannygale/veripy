"""Verilog-to-VeriPy importer using PyVerilog."""

import os
import tempfile

import pyverilog.vparser.ast as vast
import pyverilog.vparser.parser as vp

# ---------------------------------------------------------------------------
# PyVerilog AST → internal IR
# ---------------------------------------------------------------------------

_BIN_OPS = {
    vast.Plus: '+', vast.Minus: '-', vast.Times: '*', vast.Divide: '//',
    vast.Mod: '%', vast.And: '&', vast.Or: '|', vast.Xor: '^',
    vast.Sll: '<<', vast.Srl: '>>', vast.Sra: '>>',
    vast.Eq: '==', vast.NotEq: '!=', vast.Eql: '==', vast.NotEql: '!=',
    vast.LessThan: '<', vast.GreaterThan: '>', vast.LessEq: '<=', vast.GreaterEq: '>=',
    vast.Land: 'and', vast.Lor: 'or',
    vast.Power: '**', vast.Xnor: '^',
}

_UNARY_OPS = {
    vast.Unot: '~', vast.Ulnot: '!', vast.Uminus: 'neg',
    vast.Uand: '&', vast.Unand: '~&', vast.Uor: '|', vast.Unor: '~|',
    vast.Uxor: '^', vast.Uxnor: '~^',
}


def _pv_expr(node):
    if node is None:
        return ('num', 0)
    if isinstance(node, vast.Rvalue):
        return _pv_expr(node.var)
    if isinstance(node, vast.IntConst):
        v = node.value
        if "'" in v:
            _, rest = v.split("'", 1)
            rest = rest.replace('_', '')
            if rest[0] in 'hH':
                return ('num', int(rest[1:], 16))
            if rest[0] in 'bB':
                return ('num', int(rest[1:], 2))
            if rest[0] in 'dD':
                return ('num', int(rest[1:]))
            return ('num', int(rest))
        return ('num', int(v))
    if isinstance(node, vast.Identifier):
        return ('id', node.name)
    if isinstance(node, vast.Partselect):
        name = node.var.name if isinstance(node.var, vast.Identifier) else _pv_expr(node.var)
        return ('slice', name, _pv_expr(node.msb), _pv_expr(node.lsb))
    if isinstance(node, vast.Pointer):
        name = node.var.name if isinstance(node.var, vast.Identifier) else _pv_expr(node.var)
        return ('index', name, _pv_expr(node.ptr))
    if isinstance(node, vast.Concat):
        return ('concat', [_pv_expr(c) for c in node.list])
    if isinstance(node, vast.Cond):
        return ('ternary', _pv_expr(node.cond), _pv_expr(node.true_value), _pv_expr(node.false_value))
    if isinstance(node, vast.SystemCall):
        if node.syscall in ('clog2', '$clog2') and node.args:
            return ('clog2', _pv_expr(node.args[0]))
        return ('num', 0)
    op = _BIN_OPS.get(type(node))
    if op:
        return (op, _pv_expr(node.left), _pv_expr(node.right))
    op = _UNARY_OPS.get(type(node))
    if op:
        return (op, _pv_expr(node.right))
    return ('num', 0)


def _pv_width(node):
    """Return integer width or parameter name string from a Width node."""
    if node is None:
        return 1
    msb, lsb = node.msb, node.lsb
    # Common parametric pattern: [PARAM-1:0] → width = PARAM
    if (isinstance(msb, vast.Minus)
            and isinstance(msb.right, vast.IntConst) and msb.right.value == '1'
            and isinstance(lsb, vast.IntConst) and lsb.value == '0'):
        if isinstance(msb.left, vast.Identifier):
            return msb.left.name
    try:
        return int(msb.value) - int(lsb.value) + 1
    except Exception:
        return _pv_expr(msb)  # fallback: return expr tuple


def _pv_sens(senslist):
    if senslist is None:
        return 'comb'
    senses = list(senslist.list) if hasattr(senslist, 'list') else [senslist]
    result = []
    for s in senses:
        if s.type == 'all':
            return 'comb'
        edge = 'posedge' if s.type == 'posedge' else 'negedge' if s.type == 'negedge' else None
        if edge is None:
            return 'comb'
        result.append((edge, s.sig.name))
    return result


def _pv_stmts(node):
    if node is None:
        return []
    r = _pv_stmt(node)
    if isinstance(r, list):
        return r
    return [r] if r is not None else []


def _pv_stmt(node):
    if isinstance(node, (vast.BlockingSubstitution, vast.NonblockingSubstitution)):
        op = '=' if isinstance(node, vast.BlockingSubstitution) else '<='
        lv = node.left.var if isinstance(node.left, vast.Lvalue) else node.left
        rv = node.right.var if isinstance(node.right, vast.Rvalue) else node.right
        return {'type': 'assign', 'op': op, 'target': _pv_expr(lv), 'value': _pv_expr(rv)}
    if isinstance(node, vast.IfStatement):
        return {
            'type': 'if',
            'cond': _pv_expr(node.cond),
            'then': _pv_stmts(node.true_statement),
            'else': _pv_stmts(node.false_statement) if node.false_statement else None,
        }
    if isinstance(node, (vast.CaseStatement, vast.CasexStatement, vast.CasezStatement,
                         vast.UniqueCaseStatement)):
        branches, default = [], None
        for case in node.caselist:
            if case.cond is None:
                default = _pv_stmts(case.statement)
            else:
                for c in case.cond:
                    branches.append((_pv_expr(c), _pv_stmts(case.statement)))
        return {'type': 'case', 'expr': _pv_expr(node.comp), 'branches': branches, 'default': default}
    if isinstance(node, vast.ForStatement):
        try:
            var = node.pre.left.var.name
            start = int(node.pre.right.var.value)
            limit = int(node.cond.right.value)
            step = int(node.post.right.right.value)
            return {'type': 'for', 'var': var, 'start': start, 'stop': limit, 'step': step,
                    'body': _pv_stmts(node.statement)}
        except Exception:
            return None
    if isinstance(node, vast.Block):
        stmts = []
        for s in (node.statements or []):
            stmts.extend(_pv_stmts(s))
        return stmts
    return None


def _pv_to_ir(source):
    """Walk PyVerilog Source → list of module IR dicts consumed by generate()."""
    modules = []
    for moddef in source.description.definitions:
        if not isinstance(moddef, vast.ModuleDef):
            continue

        params = []
        if moddef.paramlist:
            for decl in moddef.paramlist.params:
                for item in (decl.list if hasattr(decl, 'list') else [decl]):
                    if isinstance(item, (vast.Parameter, vast.Localparam)):
                        try:
                            val = int(item.value.var.value if isinstance(item.value, vast.Rvalue)
                                      else item.value.value)
                        except Exception:
                            val = str(item.value)
                        params.append((item.name, val))

        ports = []
        if moddef.portlist:
            for ioport in moddef.portlist.ports:
                io = ioport.first if isinstance(ioport, vast.Ioport) else ioport
                if isinstance(io, (vast.Input, vast.Output)):
                    direction = 'input' if isinstance(io, vast.Input) else 'output'
                    ports.append({'name': io.name, 'dir': direction, 'width': _pv_width(io.width)})

        decls, assigns, always_blocks, instances = [], [], [], []

        for item in (moddef.items or []):
            if isinstance(item, vast.Decl):
                for d in (item.list or []):
                    if isinstance(d, (vast.Reg, vast.Wire)):
                        kind = 'reg' if isinstance(d, vast.Reg) else 'wire'
                        depth = None
                        if hasattr(d, 'dimensions') and d.dimensions:
                            try:
                                depth = int(d.dimensions.lengths[0].msb.value) + 1
                            except Exception:
                                pass
                        decls.append({'name': d.name, 'kind': kind,
                                      'width': _pv_width(d.width), 'depth': depth})
                    elif isinstance(d, vast.Integer):
                        decls.append({'name': d.name, 'kind': 'integer', 'width': 32, 'depth': None})

            elif isinstance(item, vast.Assign):
                lv = item.left.var if isinstance(item.left, vast.Lvalue) else item.left
                rv = item.right.var if isinstance(item.right, vast.Rvalue) else item.right
                assigns.append({'type': 'assign', 'op': '=', 'target': _pv_expr(lv), 'value': _pv_expr(rv)})

            elif isinstance(item, (vast.Always, vast.AlwaysComb, vast.AlwaysFF, vast.AlwaysLatch)):
                if isinstance(item, (vast.AlwaysComb, vast.AlwaysLatch)):
                    sens = 'comb'
                elif isinstance(item, vast.AlwaysFF):
                    sens = _pv_sens(item.sens_list)
                else:
                    sens = _pv_sens(item.sens_list)
                always_blocks.append({'sens': sens, 'body': _pv_stmts(item.statement)})

            elif isinstance(item, vast.InstanceList):
                for inst in item.instances:
                    p = {}
                    for pa in (inst.parameterlist or []):
                        if isinstance(pa, vast.ParamArg) and pa.paramname:
                            try:
                                p[pa.paramname] = int(pa.argname.value)
                            except Exception:
                                p[pa.paramname] = str(pa.argname)
                    port_map = {}
                    for pa in (inst.portlist or []):
                        if isinstance(pa, vast.PortArg) and pa.portname and pa.argname:
                            argname = (pa.argname.name if isinstance(pa.argname, vast.Identifier)
                                       else str(pa.argname))
                            port_map[pa.portname] = argname
                    instances.append({'mod_type': item.module, 'name': inst.name,
                                      'params': p, 'ports': port_map})

        modules.append({
            'name': moddef.name, 'params': params, 'ports': ports,
            'decls': decls, 'assigns': assigns, 'always': always_blocks, 'instances': instances,
        })
    return modules


def _parse_pv(src):
    """Parse Verilog/SV source via PyVerilog, return module IR list."""
    with tempfile.NamedTemporaryFile(suffix='.v', mode='w', delete=False) as f:
        f.write(src)
        fname = f.name
    try:
        ast, _ = vp.parse([fname])
    finally:
        os.unlink(fname)
    return _pv_to_ir(ast)


# ---------------------------------------------------------------------------
# IR → Python emitter (unchanged from original)
# ---------------------------------------------------------------------------

def _to_pascal(name):
    """snake_case → PascalCase for class names."""
    return ''.join(w.capitalize() for w in name.split('_'))


def _expr_to_py(e, self_signals):
    """Convert an expression IR node to Python source."""
    if isinstance(e, int):
        return str(e)
    if e[0] == 'num':
        v = e[1]
        if v > 255:
            return hex(v)
        return str(v)
    if e[0] == 'id':
        name = e[1]
        root = name.split('.')[0]
        if root in self_signals:
            return f'self.{name}'
        return name
    if e[0] == 'slice':
        name, hi, lo = e[1], _expr_to_py(e[2], self_signals), _expr_to_py(e[3], self_signals)
        prefix = 'self.' if name in self_signals else ''
        return f'{prefix}{name}[{hi}:{lo}]'
    if e[0] == 'index':
        name, idx = e[1], _expr_to_py(e[2], self_signals)
        prefix = 'self.' if name in self_signals else ''
        return f'{prefix}{name}[{idx}]'
    if e[0] == 'ternary':
        cond = _expr_to_py(e[1], self_signals)
        then = _expr_to_py(e[2], self_signals)
        els = _expr_to_py(e[3], self_signals)
        return f'{then} if {cond} else {els}'
    if e[0] == 'concat':
        parts = ', '.join(_expr_to_py(p, self_signals) for p in e[1])
        return f'[{parts}]'
    if e[0] == 'clog2':
        return f'clog2({_expr_to_py(e[1], self_signals)})'
    if e[0] == '~':
        return f'~{_expr_to_py(e[1], self_signals)}'
    if e[0] == '!':
        return f'not {_expr_to_py(e[1], self_signals)}'
    if e[0] == 'neg':
        return f'-{_expr_to_py(e[1], self_signals)}'
    # binary ops
    op = e[0]
    l = _expr_to_py(e[1], self_signals)
    r = _expr_to_py(e[2], self_signals)
    return f'({l} {op} {r})'


def _target_to_py(e, self_signals):
    return _expr_to_py(e, self_signals)


def _stmts_to_py(stmts, self_signals, indent):
    """Convert a list of statement IR nodes to Python lines."""
    pad = '    ' * indent
    lines = []
    for s in stmts:
        if s['type'] == 'assign':
            t = _target_to_py(s['target'], self_signals)
            v = _expr_to_py(s['value'], self_signals)
            lines.append(f'{pad}{t} = {v}')
        elif s['type'] == 'if':
            cond = _expr_to_py(s['cond'], self_signals)
            lines.append(f'{pad}if {cond}:')
            lines += _stmts_to_py(s['then'], self_signals, indent + 1)
            if s['else']:
                rest = s['else']
                if len(rest) == 1 and rest[0]['type'] == 'if':
                    lines.append(f'{pad}elif {_expr_to_py(rest[0]["cond"], self_signals)}:')
                    lines += _stmts_to_py(rest[0]['then'], self_signals, indent + 1)
                    if rest[0]['else']:
                        inner = rest[0]['else']
                        while len(inner) == 1 and inner[0]['type'] == 'if':
                            lines.append(f'{pad}elif {_expr_to_py(inner[0]["cond"], self_signals)}:')
                            lines += _stmts_to_py(inner[0]['then'], self_signals, indent + 1)
                            inner = inner[0]['else'] or []
                        if inner:
                            lines.append(f'{pad}else:')
                            lines += _stmts_to_py(inner, self_signals, indent + 1)
                else:
                    lines.append(f'{pad}else:')
                    lines += _stmts_to_py(rest, self_signals, indent + 1)
        elif s['type'] == 'case':
            expr = _expr_to_py(s['expr'], self_signals)
            for i, (label, body) in enumerate(s['branches']):
                kw = 'if' if i == 0 else 'elif'
                lines.append(f'{pad}{kw} {expr} == {_expr_to_py(label, self_signals)}:')
                lines += _stmts_to_py(body, self_signals, indent + 1)
            if s['default']:
                lines.append(f'{pad}else:')
                lines += _stmts_to_py(s['default'], self_signals, indent + 1)
        elif s['type'] == 'for':
            lines.append(f'{pad}for {s["var"]} in range({s["start"]}, {s["stop"]}, {s["step"]}):')
            lines += _stmts_to_py(s['body'], self_signals, indent + 1)
    return lines


def generate(modules, external=None):
    """Generate VeriPy Python source from parsed module IR.

    external: optional dict of module_name → (relative_import_path, ClassName)
              for modules defined in other files.
    """
    external = external or {}
    mod_types = {m['name'] for m in modules} | set(external)

    lines = ['from veripy import Module, Input, Output, Register, Signal, Mem, posedge, negedge']
    if any(any(('clog2', ) == (e[0],) if isinstance(e, tuple) else False
               for ab in m['always'] for s in ab['body']
               for e in [s.get('value', ('num', 0))])
           for m in modules):
        lines[0] += '\nfrom veripy.parameter import clog2'
    for mod_name, (imp_path, cls_name) in sorted(external.items()):
        lines.append(f'from {imp_path} import {cls_name}')
    lines.append('')

    for mod in modules:
        class_name = _to_pascal(mod['name'])

        self_signals = set()
        for p in mod['ports']:
            self_signals.add(p['name'])
        for d in mod['decls']:
            if d['kind'] != 'integer':
                self_signals.add(d['name'])
        inst_wires = {}
        for inst in mod['instances']:
            for port, wire in inst['ports'].items():
                inst_wires[wire] = (inst['name'], port)
            self_signals.add(inst['name'])

        param_args = ', '.join(f'{n}={v}' for n, v in mod['params'])
        ctor_sig = f'self, {param_args}' if param_args else 'self'

        lines.append(f'class {class_name}(Module):')
        lines.append(f'    def __init__({ctor_sig}):')

        for p in mod['ports']:
            w = p['width']
            w_arg = '' if w == 1 else str(w)
            if p['dir'] == 'input':
                lines.append(f'        self.{p["name"]} = Input({w_arg})')
            else:
                lines.append(f'        self.{p["name"]} = Output({w_arg})')

        inst_wire_names = {wire for inst in mod['instances'] for wire in inst['ports'].values()}
        for d in mod['decls']:
            if d['kind'] == 'integer' or d['name'] in inst_wire_names:
                continue
            w = d['width']
            w_arg = '' if w == 1 else str(w)
            if d['depth'] is not None:
                lines.append(f'        self.{d["name"]} = Mem({d["depth"]}, {w})')
            elif d['kind'] == 'reg':
                lines.append(f'        self.{d["name"]} = Register({w_arg})')
            else:
                lines.append(f'        self.{d["name"]} = Signal({w_arg})')

        for inst in mod['instances']:
            cls = _to_pascal(inst['mod_type'])
            if inst['params']:
                kwargs = ', '.join(f'{k}={v}' for k, v in inst['params'].items())
                lines.append(f'        self.{inst["name"]} = {cls}({kwargs})')
            else:
                lines.append(f'        self.{inst["name"]} = {cls}()')

        lines.append('        super().__init__()')

        def _rewrite_wire(expr):
            if isinstance(expr, tuple):
                if expr[0] == 'id' and expr[1] in inst_wires:
                    inst_name, port = inst_wires[expr[1]]
                    return ('id', f'{inst_name}.{port}')
                return tuple(_rewrite_wire(x) if isinstance(x, tuple) else x for x in expr)
            return expr

        port_names = {p['name'] for p in mod['ports']}
        direct_wires = [(inst['name'], port, wire)
                        for inst in mod['instances']
                        for port, wire in inst['ports'].items()
                        if wire in port_names]

        if mod['assigns'] or direct_wires:
            lines.append('')
            lines.append('        @self.comb')
            lines.append('        def _assign():')
            for a in mod['assigns']:
                t = _rewrite_wire(a['target'])
                v = _rewrite_wire(a['value'])
                lines.append(f'            {_target_to_py(t, self_signals)} = {_expr_to_py(v, self_signals)}')
            for inst_name, port, wire in direct_wires:
                lines.append(f'            self.{inst_name}.{port} = self.{wire}')

        for i, ab in enumerate(mod['always']):
            lines.append('')
            sens = ab['sens']
            if sens == 'comb':
                lines.append('        @self.comb')
                lines.append(f'        def _always_{i}():')
            elif len(sens) == 1:
                edge_type, sig = sens[0]
                lines.append(f'        @self.{edge_type}(self.{sig})')
                lines.append(f'        def _always_{i}():')
            else:
                parts = ' | '.join(f'{et}(self.{sig})' for et, sig in sens)
                lines.append(f'        @self.always({parts})')
                lines.append(f'        def _always_{i}():')

            body = [_rewrite_stmt(s, inst_wires) for s in ab['body']]
            lines += _stmts_to_py(body, self_signals, 3)

        lines.append('')
        lines.append('')

    return '\n'.join(lines)


def _rewrite_stmt(stmt, inst_wires):
    if stmt['type'] == 'assign':
        return {
            'type': 'assign', 'op': stmt['op'],
            'target': _rewrite_expr(stmt['target'], inst_wires),
            'value': _rewrite_expr(stmt['value'], inst_wires),
        }
    if stmt['type'] == 'if':
        return {
            'type': 'if',
            'cond': _rewrite_expr(stmt['cond'], inst_wires),
            'then': [_rewrite_stmt(s, inst_wires) for s in stmt['then']],
            'else': [_rewrite_stmt(s, inst_wires) for s in stmt['else']] if stmt['else'] else None,
        }
    if stmt['type'] == 'case':
        return {
            'type': 'case',
            'expr': _rewrite_expr(stmt['expr'], inst_wires),
            'branches': [(l, [_rewrite_stmt(s, inst_wires) for s in b]) for l, b in stmt['branches']],
            'default': [_rewrite_stmt(s, inst_wires) for s in stmt['default']] if stmt['default'] else None,
        }
    return stmt


def _rewrite_expr(expr, inst_wires):
    if not isinstance(expr, tuple):
        return expr
    if expr[0] == 'id' and expr[1] in inst_wires:
        inst_name, port = inst_wires[expr[1]]
        return ('id', f'{inst_name}.{port}')
    return tuple(_rewrite_expr(x, inst_wires) if isinstance(x, tuple) else x for x in expr)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_verilog(src):
    """Parse Verilog/SV source and return equivalent VeriPy Python source."""
    return generate(_parse_pv(src))


def import_project(src_dir):
    """Import a directory of Verilog files with cross-file module resolution.

    Returns dict of {relative_py_path: python_source}.
    """
    file_modules = {}
    for root, _dirs, files in os.walk(src_dir):
        for f in sorted(files):
            if not f.endswith(('.v', '.sv')):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, src_dir)
            with open(full) as fh:
                file_modules[rel] = _parse_pv(fh.read())

    registry = {}
    for rel, mods in file_modules.items():
        for m in mods:
            registry[m['name']] = (rel, _to_pascal(m['name']))

    result = {}
    for rel, mods in sorted(file_modules.items()):
        py_rel = rel.replace('.sv', '.py').replace('.v', '.py')
        local_names = {m['name'] for m in mods}
        external = {}
        for m in mods:
            for inst in m['instances']:
                mt = inst['mod_type']
                if mt not in local_names and mt in registry:
                    v_path, cls = registry[mt]
                    mod_path = '.' + v_path.replace('.sv', '').replace('.v', '').replace(os.sep, '.')
                    external[mt] = (mod_path, cls)
        result[py_rel] = generate(mods, external)

    return result
