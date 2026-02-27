"""Verilog-to-VeriPy importer: parse the veripy Verilog subset and emit Python."""

import re

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_KEYWORDS = {
    'module', 'endmodule', 'input', 'output', 'reg', 'wire', 'parameter',
    'assign', 'always', 'posedge', 'negedge', 'if', 'else', 'begin', 'end',
    'case', 'endcase', 'default', 'initial', 'integer', 'for',
}

_TOKEN_RE = re.compile(r"""
    (?P<COMMENT>//[^\n]*)                |
    (?P<BLOCK_COMMENT>/\*[\s\S]*?\*/)    |
    (?P<HEX>\d+'h[0-9a-fA-F_]+)         |
    (?P<BIN>\d+'b[01_]+)                 |
    (?P<NUM>\d+)                         |
    (?P<ID>[a-zA-Z_]\w*)                 |
    (?P<LE><=)                           |
    (?P<EQ>==)                           |
    (?P<NE>!=)                           |
    (?P<LSHIFT><<)                       |
    (?P<RSHIFT>>>)                       |
    (?P<GE>>=)                           |
    (?P<SYM>[(){}\[\];:,@.?~&|^+\-*/<>=!#]) |
    (?P<WS>\s+)
""", re.VERBOSE)


def tokenize(src):
    tokens = []
    for m in _TOKEN_RE.finditer(src):
        kind = m.lastgroup
        val = m.group()
        if kind in ('WS', 'COMMENT', 'BLOCK_COMMENT'):
            continue
        if kind == 'ID' and val in _KEYWORDS:
            kind = 'KW'
        tokens.append((kind, val))
    return tokens


# ---------------------------------------------------------------------------
# Parser — produces a list of module IR dicts
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset=0):
        p = self.pos + offset
        return self.tokens[p] if p < len(self.tokens) else ('EOF', '')

    def eat(self, expected_val=None):
        t = self.tokens[self.pos]
        if expected_val and t[1] != expected_val:
            raise SyntaxError(f"Expected '{expected_val}', got '{t[1]}' at token {self.pos}")
        self.pos += 1
        return t

    def at(self, val):
        return self.pos < len(self.tokens) and self.tokens[self.pos][1] == val

    def at_kind(self, kind):
        return self.pos < len(self.tokens) and self.tokens[self.pos][0] == kind

    def parse(self):
        modules = []
        while self.pos < len(self.tokens):
            if self.at('module'):
                modules.append(self._module())
            else:
                self.pos += 1  # skip stray tokens
        return modules

    def _module(self):
        self.eat('module')
        name = self.eat()[1]
        params = []
        if self.at('#'):
            self.eat('#')
            self.eat('(')
            params = self._param_list()
            self.eat(')')
        self.eat('(')
        ports = self._port_list()
        self.eat(')')
        self.eat(';')
        decls, assigns, always_blocks, instances = [], [], [], []
        while not self.at('endmodule'):
            if self.at('reg') or self.at('wire') or self.at('integer'):
                decls.append(self._declaration())
            elif self.at('assign'):
                assigns.append(self._assign())
            elif self.at('always'):
                always_blocks.append(self._always())
            elif self.at('initial'):
                self._skip_initial()
            elif self._is_instance():
                instances.append(self._instance())
            else:
                self.pos += 1
        self.eat('endmodule')
        return {
            'name': name, 'params': params, 'ports': ports,
            'decls': decls, 'assigns': assigns,
            'always': always_blocks, 'instances': instances,
        }

    def _param_list(self):
        params = []
        while not self.at(')'):
            if self.at('parameter'):
                self.eat('parameter')
            name = self.eat()[1]
            self.eat('=')
            val = self._const_expr()
            params.append((name, val))
            if self.at(','):
                self.eat(',')
        return params

    def _port_list(self):
        ports = []
        while not self.at(')'):
            direction = self.eat()[1]  # input/output
            is_reg = False
            if self.at('reg'):
                self.eat('reg')
                is_reg = True
            width = self._opt_width()
            name = self.eat()[1]
            ports.append({'dir': direction, 'name': name, 'width': width, 'reg': is_reg})
            if self.at(','):
                self.eat(',')
        return ports

    def _opt_width(self):
        if self.at('['):
            self.eat('[')
            hi = self._expr()
            self.eat(':')
            lo = self._expr()
            self.eat(']')
            try:
                hi_val = self._eval_const(hi)
                lo_val = self._eval_const(lo)
                return hi_val - lo_val + 1
            except SyntaxError:
                # Parametric width — detect [param-1:0] → param
                lo_val = self._try_eval(lo)
                if lo_val == 0 and isinstance(hi, tuple) and hi[0] == '-':
                    one = self._try_eval(hi[2])
                    if one == 1:
                        return self._expr_str(hi[1])
                return self._expr_str(('+'  , ('-', hi, lo), ('num', 1)))
        return 1

    def _try_eval(self, e):
        try:
            return self._eval_const(e)
        except SyntaxError:
            return None

    def _expr_str(self, e):
        """Convert expression AST to Python string."""
        if isinstance(e, int):
            return str(e)
        if e[0] == 'num':
            return str(e[1])
        if e[0] == 'id':
            return e[1]
        if e[0] in ('+', '-', '*'):
            return f'({self._expr_str(e[1])} {e[0]} {self._expr_str(e[2])})'
        return str(e)

    def _declaration(self):
        kind = self.eat()[1]  # reg, wire, integer
        width = self._opt_width()
        name = self.eat()[1]
        # Check for memory: reg [w] name [0:d-1]
        mem_depth = None
        if self.at('['):
            self.eat('[')
            lo = self._const_expr()
            self.eat(':')
            hi = self._const_expr()
            self.eat(']')
            mem_depth = hi - lo + 1
        self.eat(';')
        return {'kind': kind, 'name': name, 'width': width, 'depth': mem_depth}

    def _assign(self):
        self.eat('assign')
        target = self._expr()
        self.eat('=')
        value = self._expr()
        self.eat(';')
        return {'target': target, 'value': value}

    def _always(self):
        self.eat('always')
        self.eat('@')
        sens = self._sensitivity()
        body = self._block_or_stmt()
        return {'sens': sens, 'body': body}

    def _sensitivity(self):
        self.eat('(')
        if self.at('*'):
            self.eat('*')
            self.eat(')')
            return 'comb'
        edges = []
        while not self.at(')'):
            edge_type = self.eat()[1]  # posedge/negedge
            sig = self.eat()[1]
            edges.append((edge_type, sig))
            if self.at('or'):
                self.eat('or')
        self.eat(')')
        return edges

    def _block_or_stmt(self):
        if self.at('begin'):
            self.eat('begin')
            stmts = []
            while not self.at('end'):
                stmts.append(self._stmt())
            self.eat('end')
            return stmts
        return [self._stmt()]

    def _stmt(self):
        if self.at('if'):
            return self._if_stmt()
        if self.at('case'):
            return self._case_stmt()
        if self.at('for'):
            return self._for_stmt()
        # assignment: target <= expr; or target = expr;
        target = self._expr()
        if self.peek()[0] == 'LE':  # <=
            self.eat()
            op = '<='
        else:
            self.eat('=')
            op = '='
        value = self._expr()
        self.eat(';')
        return {'type': 'assign', 'op': op, 'target': target, 'value': value}

    def _if_stmt(self):
        self.eat('if')
        self.eat('(')
        cond = self._expr()
        self.eat(')')
        then = self._block_or_stmt()
        els = None
        if self.at('else'):
            self.eat('else')
            if self.at('if'):
                els = [self._if_stmt()]
            else:
                els = self._block_or_stmt()
        return {'type': 'if', 'cond': cond, 'then': then, 'else': els}

    def _case_stmt(self):
        self.eat('case')
        self.eat('(')
        expr = self._expr()
        self.eat(')')
        branches = []
        default = None
        while not self.at('endcase'):
            if self.at('default'):
                self.eat('default')
                self.eat(':')
                default = self._block_or_stmt()
            else:
                label = self._const_expr()
                self.eat(':')
                body = self._block_or_stmt()
                branches.append((label, body))
        self.eat('endcase')
        return {'type': 'case', 'expr': expr, 'branches': branches, 'default': default}

    def _for_stmt(self):
        self.eat('for')
        self.eat('(')
        # init: var = expr
        var = self.eat()[1]
        self.eat('=')
        init = self._const_expr()
        self.eat(';')
        # cond: var < expr
        self.eat()  # var
        self.eat()  # <
        limit = self._const_expr()
        self.eat(';')
        # incr: var = var + step
        self.eat()  # var
        self.eat('=')
        self.eat()  # var
        self.eat('+')
        step = self._const_expr()
        self.eat(')')
        body = self._block_or_stmt()
        return {'type': 'for', 'var': var, 'start': init, 'stop': limit, 'step': step, 'body': body}

    def _is_instance(self):
        """Peek ahead to detect module instantiation: id [#(...)] id (...)"""
        if not self.at_kind('ID'):
            return False
        i = 1
        if self.peek(i)[1] == '#':
            # skip #(...)
            i += 1
            if self.peek(i)[1] != '(':
                return False
            depth = 1
            i += 1
            while depth > 0 and self.peek(i)[1] != '':
                if self.peek(i)[1] == '(':
                    depth += 1
                elif self.peek(i)[1] == ')':
                    depth -= 1
                i += 1
        # Next should be an identifier (instance name)
        if not (self.peek(i)[0] == 'ID'):
            return False
        # Then (
        return self.peek(i + 1)[1] == '('

    def _instance(self):
        mod_type = self.eat()[1]
        params = {}
        if self.at('#'):
            self.eat('#')
            self.eat('(')
            while not self.at(')'):
                self.eat('.')
                pname = self.eat()[1]
                self.eat('(')
                e = self._expr()
                try:
                    pval = self._eval_const(e)
                except SyntaxError:
                    pval = self._expr_str(e)
                self.eat(')')
                params[pname] = pval
                if self.at(','):
                    self.eat(',')
            self.eat(')')
        inst_name = self.eat()[1]
        self.eat('(')
        port_map = {}
        while not self.at(')'):
            self.eat('.')
            port = self.eat()[1]
            self.eat('(')
            wire = self.eat()[1]
            self.eat(')')
            port_map[port] = wire
            if self.at(','):
                self.eat(',')
        self.eat(')')
        self.eat(';')
        return {'mod_type': mod_type, 'name': inst_name, 'params': params, 'ports': port_map}

    def _skip_initial(self):
        """Skip initial blocks (mem zero-init etc)."""
        self.eat('initial')
        self._block_or_stmt()

    # --- expression parsing (precedence climbing) ---

    def _expr(self):
        return self._ternary()

    def _ternary(self):
        e = self._or()
        if self.at('?'):
            self.eat('?')
            then = self._expr()
            self.eat(':')
            els = self._expr()
            return ('ternary', e, then, els)
        return e

    def _or(self):
        e = self._xor()
        while self.at('|'):
            self.eat('|')
            e = ('|', e, self._xor())
        return e

    def _xor(self):
        e = self._and()
        while self.at('^'):
            self.eat('^')
            e = ('^', e, self._and())
        return e

    def _and(self):
        e = self._equality()
        while self.at('&'):
            self.eat('&')
            e = ('&', e, self._equality())
        return e

    def _equality(self):
        e = self._comparison()
        while self.peek()[1] in ('==', '!='):
            op = self.eat()[1]
            e = (op, e, self._comparison())
        return e

    def _comparison(self):
        e = self._shift()
        while self.peek()[1] in ('<', '>') or self.peek()[0] == 'GE':
            op = self.eat()[1]
            e = (op, e, self._shift())
        return e

    def _shift(self):
        e = self._add()
        while self.peek()[1] in ('<<', '>>'):
            op = self.eat()[1]
            e = (op, e, self._add())
        return e

    def _add(self):
        e = self._mul()
        while self.peek()[1] in ('+', '-'):
            op = self.eat()[1]
            e = (op, e, self._mul())
        return e

    def _mul(self):
        e = self._unary()
        while self.at('*'):
            self.eat('*')
            e = ('*', e, self._unary())
        return e

    def _unary(self):
        if self.at('~'):
            self.eat('~')
            return ('~', self._unary())
        if self.at('!'):
            self.eat('!')
            return ('!', self._unary())
        if self.at('-') and self.peek(1)[0] in ('NUM', 'HEX', 'BIN'):
            self.eat('-')
            return ('neg', self._primary())
        return self._primary()

    def _primary(self):
        t = self.peek()
        if t[0] == 'NUM':
            self.eat()
            return ('num', int(t[1]))
        if t[0] == 'HEX':
            self.eat()
            return ('num', int(t[1].split("'h")[1].replace('_', ''), 16))
        if t[0] == 'BIN':
            self.eat()
            return ('num', int(t[1].split("'b")[1].replace('_', ''), 2))
        if t[1] == '(':
            self.eat('(')
            e = self._expr()
            self.eat(')')
            return e
        if t[1] == '{':
            return self._concat()
        if t[0] in ('ID', 'KW'):
            name = self.eat()[1]
            if self.at('['):
                self.eat('[')
                hi = self._expr()
                if self.at(':'):
                    self.eat(':')
                    lo = self._expr()
                    self.eat(']')
                    return ('slice', name, hi, lo)
                self.eat(']')
                return ('index', name, hi)
            return ('id', name)
        raise SyntaxError(f"Unexpected token: {t} at pos {self.pos}")

    def _concat(self):
        self.eat('{')
        parts = [self._expr()]
        while self.at(','):
            self.eat(',')
            parts.append(self._expr())
        self.eat('}')
        return ('concat', parts)

    def _const_expr(self):
        """Parse a constant expression and evaluate to int."""
        e = self._expr()
        return self._eval_const(e)

    def _eval_const(self, e):
        if isinstance(e, int):
            return e
        if e[0] == 'num':
            return e[1]
        if e[0] == '+':
            return self._eval_const(e[1]) + self._eval_const(e[2])
        if e[0] == '-':
            return self._eval_const(e[1]) - self._eval_const(e[2])
        if e[0] == '*':
            return self._eval_const(e[1]) * self._eval_const(e[2])
        raise SyntaxError(f"Cannot evaluate constant: {e}")


def parse(src):
    """Parse Verilog source into a list of module IR dicts."""
    return _Parser(tokenize(src)).parse()


# ---------------------------------------------------------------------------
# Code generator — IR → VeriPy Python source
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
        # Dotted names like alu.result → self.alu.result
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
    """Convert an assignment target to Python."""
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
                if len(s['else']) == 1 and s['else'][0]['type'] == 'if':
                    lines.append(f'{pad}elif {_expr_to_py(s["else"][0]["cond"], self_signals)}:')
                    lines += _stmts_to_py(s['else'][0]['then'], self_signals, indent + 1)
                    if s['else'][0]['else']:
                        # Recurse for further elif/else
                        rest = s['else'][0]
                        while rest.get('else') and len(rest['else']) == 1 and rest['else'][0]['type'] == 'if':
                            rest = rest['else'][0]
                            lines.append(f'{pad}elif {_expr_to_py(rest["cond"], self_signals)}:')
                            lines += _stmts_to_py(rest['then'], self_signals, indent + 1)
                        if rest.get('else') and not (len(rest['else']) == 1 and rest['else'][0]['type'] == 'if'):
                            lines.append(f'{pad}else:')
                            lines += _stmts_to_py(rest['else'], self_signals, indent + 1)
                else:
                    lines.append(f'{pad}else:')
                    lines += _stmts_to_py(s['else'], self_signals, indent + 1)
        elif s['type'] == 'case':
            expr = _expr_to_py(s['expr'], self_signals)
            for i, (label, body) in enumerate(s['branches']):
                kw = 'if' if i == 0 else 'elif'
                lines.append(f'{pad}{kw} {expr} == {label}:')
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
    # Build a set of known module types for instance detection
    mod_types = {m['name'] for m in modules} | set(external)

    lines = ['from veripy import Module, Input, Output, Register, Signal, Mem, posedge, negedge']
    # Cross-file imports for externally defined modules
    for mod_name, (imp_path, cls_name) in sorted(external.items()):
        lines.append(f'from {imp_path} import {cls_name}')
    lines.append('')

    for mod in modules:
        class_name = _to_pascal(mod['name'])

        # Collect all signal names for self. prefixing
        self_signals = set()
        for p in mod['ports']:
            self_signals.add(p['name'])
        for d in mod['decls']:
            if d['kind'] != 'integer':
                self_signals.add(d['name'])
        # Instance wires: sub_port → sub.port
        inst_wires = {}  # wire_name → (inst_name, port_name)
        for inst in mod['instances']:
            for port, wire in inst['ports'].items():
                inst_wires[wire] = (inst['name'], port)
            self_signals.add(inst['name'])

        # Constructor params
        param_args = ', '.join(f'{n}={v}' for n, v in mod['params'])
        ctor_sig = f'self, {param_args}' if param_args else 'self'

        lines.append(f'class {class_name}(Module):')
        lines.append(f'    def __init__({ctor_sig}):')

        # Ports
        for p in mod['ports']:
            w = p['width']
            w_arg = '' if w == 1 else str(w)
            if p['dir'] == 'input':
                lines.append(f'        self.{p["name"]} = Input({w_arg})')
            else:
                lines.append(f'        self.{p["name"]} = Output({w_arg})')

        # Internal declarations
        # Internal declarations (skip wires that are instance port wiring)
        inst_wire_names = set()
        for inst in mod['instances']:
            for port, wire in inst['ports'].items():
                inst_wire_names.add(wire)

        for d in mod['decls']:
            if d['kind'] == 'integer':
                continue
            if d['name'] in inst_wire_names:
                continue  # skip instance wiring artifacts
            w = d['width']
            w_arg = '' if w == 1 else str(w)
            if d['depth'] is not None:
                lines.append(f'        self.{d["name"]} = Mem({d["depth"]}, {w})')
            elif d['kind'] == 'reg':
                lines.append(f'        self.{d["name"]} = Register({w_arg})')
            else:
                lines.append(f'        self.{d["name"]} = Signal({w_arg})')

        # Sub-module instances
        for inst in mod['instances']:
            cls = _to_pascal(inst['mod_type'])
            if inst['params']:
                kwargs = ', '.join(f'{k}={v}' for k, v in inst['params'].items())
                lines.append(f'        self.{inst["name"]} = {cls}({kwargs})')
            else:
                lines.append(f'        self.{inst["name"]} = {cls}()')

        lines.append('        super().__init__()')

        # Rewrite assigns that wire to sub-module ports
        def _rewrite_wire(expr):
            """Replace inst_port wire references with self.inst.port."""
            if isinstance(expr, tuple):
                if expr[0] == 'id' and expr[1] in inst_wires:
                    inst_name, port = inst_wires[expr[1]]
                    return ('id', f'{inst_name}.{port}')
                return tuple(_rewrite_wire(x) if isinstance(x, tuple) else x for x in expr)
            return expr

        # Generate direct port wiring for sub-module connections
        # e.g. .a(x) where x is a top-level port → self.inst.a = self.x
        port_names = {p['name'] for p in mod['ports']}
        direct_wires = []
        for inst in mod['instances']:
            for port, wire in inst['ports'].items():
                if wire in port_names:
                    direct_wires.append((inst['name'], port, wire))

        # Group assigns into comb blocks
        has_assigns = mod['assigns'] or direct_wires
        if has_assigns:
            lines.append('')
            lines.append('        @self.comb')
            lines.append('        def _assign():')
            for a in mod['assigns']:
                t = _rewrite_wire(a['target'])
                v = _rewrite_wire(a['value'])
                ts = _target_to_py(t, self_signals)
                vs = _expr_to_py(v, self_signals)
                lines.append(f'            {ts} = {vs}')
            for inst_name, port, wire in direct_wires:
                lines.append(f'            self.{inst_name}.{port} = self.{wire}')

        # Always blocks
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

            # Rewrite body
            body = []
            for s in ab['body']:
                body.append(_rewrite_stmt(s, inst_wires))
            body_lines = _stmts_to_py(body, self_signals, 3)
            lines += body_lines

        lines.append('')
        lines.append('')

    return '\n'.join(lines)


def _rewrite_stmt(stmt, inst_wires):
    """Recursively rewrite wire references in statements."""
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
    """Replace inst_port wire references with inst.port paths."""
    if not isinstance(expr, tuple):
        return expr
    if expr[0] == 'id' and expr[1] in inst_wires:
        inst_name, port = inst_wires[expr[1]]
        return ('id', f'{inst_name}.{port}')
    return tuple(_rewrite_expr(x, inst_wires) if isinstance(x, tuple) else x for x in expr)


def import_verilog(src):
    """Parse Verilog source and return equivalent VeriPy Python source."""
    return generate(parse(src))


def import_project(src_dir):
    """Import a directory of Verilog files with cross-file module resolution.

    Returns dict of {relative_py_path: python_source}.
    """
    import os

    # 1. Discover and parse all .v files
    file_modules = {}  # rel_path → [parsed modules]
    for root, _dirs, files in os.walk(src_dir):
        for f in sorted(files):
            if not f.endswith('.v'):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, src_dir)
            with open(full) as fh:
                file_modules[rel] = parse(fh.read())

    # 2. Build global registry: module_name → (v_rel_path, class_name)
    registry = {}
    for rel, mods in file_modules.items():
        for m in mods:
            registry[m['name']] = (rel, _to_pascal(m['name']))

    # 3. Generate Python for each file with cross-file imports
    result = {}
    for rel, mods in sorted(file_modules.items()):
        py_rel = rel.replace('.v', '.py')
        local_names = {m['name'] for m in mods}

        # Find external modules referenced by instances in this file
        external = {}
        for m in mods:
            for inst in m['instances']:
                mt = inst['mod_type']
                if mt not in local_names and mt in registry:
                    v_path, cls = registry[mt]
                    mod_path = '.' + v_path.replace('.v', '').replace(os.sep, '.')
                    external[mt] = (mod_path, cls)

        result[py_rel] = generate(mods, external)

    return result
