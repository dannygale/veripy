"""Verilog emitter: parses Python AST of @posedge/@comb methods and emits Verilog."""

import ast
import inspect
import textwrap

from .signal import Signal, Mem


# Python AST op → Verilog operator
_BIN_OPS = {
    ast.Add: '+', ast.Sub: '-', ast.Mult: '*',
    ast.BitAnd: '&', ast.BitOr: '|', ast.BitXor: '^',
    ast.LShift: '<<', ast.RShift: '>>',
    ast.Mod: '%',
}
_CMP_OPS = {
    ast.Eq: '==', ast.NotEq: '!=',
    ast.Lt: '<', ast.LtE: '<=', ast.Gt: '>', ast.GtE: '>=',
}
_UNARY_OPS = {
    ast.Invert: '~', ast.USub: '-', ast.Not: '!',
}
_BOOL_OPS = {ast.And: '&&', ast.Or: '||'}


class VerilogEmitter:
    def __init__(self, module, module_name=None):
        self.mod = module
        self.name = module_name or type(module).__name__.lower()
        self._current_func = None
        self._locals = {}  # local variable → AST expression (inline substitution)
        self.signals = {}
        self.mems = {}
        self.submodules = {}
        for k in dir(module):
            v = getattr(module, k)
            if isinstance(v, Mem):
                self.mems[k] = v
            elif isinstance(v, Signal):
                self.signals[k] = v
        from .module import Module
        for k in dir(module):
            if k.startswith('_'):
                continue
            v = getattr(module, k)
            if isinstance(v, Module) and v is not module:
                self.submodules[k] = v

    def emit(self):
        lines = []
        lines += self._emit_header()
        lines += self._emit_internals()
        lines += self._emit_submodule_instances()
        for method in self.mod._comb_blocks:
            lines += self._emit_comb(method)
        for clk, method in self.mod._posedge_blocks:
            lines += self._emit_posedge(clk, method)
        lines.append('endmodule')
        return '\n'.join(lines)

    def emit_all(self):
        """Emit this module + all sub-module definitions."""
        parts = []
        for sub_name, sub in self.submodules.items():
            parts.append(sub.to_verilog(module_name=sub_name))
        parts.append(self.emit())
        return '\n\n'.join(parts)

    # --- header: module declaration + ports ---
    def _emit_header(self):
        ports = self._port_list()
        params = self.mod._params
        if params:
            lines = [f'module {self.name} #(']
            param_lines = [f'    parameter {name} = {val}' for name, val in params.items()]
            lines.append(',\n'.join(param_lines))
            lines.append(') (')
        else:
            lines = [f'module {self.name} (']
        port_lines = []
        for name, sig, direction in ports:
            w = self._width_str(sig)
            port_lines.append(f'    {direction} {w}{name}')
        lines.append(',\n'.join(port_lines))
        lines.append(');')
        return lines

    def _emit_internals(self):
        lines = ['']
        for name, sig in sorted(self.signals.items()):
            if sig._kind in ('input', 'output'):
                continue
            w = self._width_str(sig)
            lines.append(f'    reg {w}{name};')
        for name, mem in sorted(self.mems.items()):
            w = f'[{mem.width - 1}:0] ' if mem.width > 1 else ''
            lines.append(f'    reg {w}{name} [0:{mem.depth - 1}];')
        # Initialize memories to zero (match Python sim behavior)
        if self.mems:
            lines.append('    integer _i;')
            lines.append('    initial begin')
            for name, mem in sorted(self.mems.items()):
                lines.append(f'        for (_i = 0; _i < {mem.depth}; _i = _i + 1)')
                lines.append(f'            {name}[_i] = 0;')
            lines.append('    end')
        # Wires for sub-module ports
        for sub_name, sub in sorted(self.submodules.items()):
            for port_name in sorted(dir(sub)):
                sig = getattr(sub, port_name)
                if isinstance(sig, Signal) and sig._kind in ('input', 'output'):
                    w = self._width_str(sig)
                    lines.append(f'    wire {w}{sub_name}_{port_name};')
        lines.append('')
        return lines

    def _emit_submodule_instances(self):
        lines = []
        for sub_name, sub in sorted(self.submodules.items()):
            mod_type = sub_name  # instance type = attribute name (matches emit_all)
            inst_name = f'{sub_name}_inst'
            ports = []
            for port_name in sorted(dir(sub)):
                sig = getattr(sub, port_name)
                if isinstance(sig, Signal) and sig._kind in ('input', 'output'):
                    ports.append(f'.{port_name}({sub_name}_{port_name})')
            lines.append(f'    {mod_type} {inst_name} (')
            lines.append(',\n'.join(f'        {p}' for p in ports))
            lines.append('    );')
            lines.append('')
        return lines

    def _port_list(self):
        # Determine which outputs are driven by simple assigns (not always @(*))
        assign_driven = set()
        for method in self.mod._comb_blocks:
            tree = self._get_func_ast(method)
            if all(self._is_nba(s) for s in tree.body):
                self._current_func = method; self._locals = {}
                for stmt in tree.body:
                    t, _ = self._extract_nba(stmt)
                    assign_driven.add(t.split('[')[0])  # strip bit index
                self._current_func = None

        ports = []
        for name, sig in sorted(self.signals.items()):
            if sig._kind == 'input':
                ports.append((name, sig, 'input'))
        for name, sig in sorted(self.signals.items()):
            if sig._kind == 'output':
                kind = 'output' if name in assign_driven else 'output reg'
                ports.append((name, sig, kind))
        return ports

    def _width_str(self, sig):
        if sig.width == 1:
            return ''
        return f'[{sig.width - 1}:0] '

    # --- comb blocks → assign or always @(*) ---
    def _emit_comb(self, method):
        tree = self._get_func_ast(method)
        # Simple: all statements are bare assigns → use assign
        if all(self._is_nba(s) for s in tree.body):
            self._current_func = method; self._locals = {}
            lines = []
            for stmt in tree.body:
                t, v = self._extract_nba(stmt)
                lines.append(f'    assign {t} = {v};')
            lines.append('')
            self._current_func = None
            return lines
        # Complex: has control flow → always @(*)
        self._current_func = method; self._locals = {}
        lines = ['    always @(*) begin']
        lines += self._stmts_to_v(tree.body, indent=2, assign_op='=')
        lines += ['    end', '']
        self._current_func = None
        return lines

    # --- posedge blocks → always @(posedge clk) ---
    def _emit_posedge(self, clk, method):
        tree = self._get_func_ast(method)
        self._current_func = method; self._locals = {}
        lines = [f'    always @(posedge {clk.name}) begin']
        lines += self._stmts_to_v(tree.body, indent=2)
        lines += ['    end', '']
        self._current_func = None
        return lines

    # --- statement translation ---
    def _stmts_to_v(self, stmts, indent, assign_op='<='):
        pad = '    ' * indent
        lines = []
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                case = self._try_case_chain(stmt)
                if case:
                    lines += self._emit_case(case, indent, assign_op)
                else:
                    lines += self._emit_if(stmt, indent, assign_op)
            elif isinstance(stmt, ast.For):
                lines += self._emit_for(stmt, indent, assign_op)
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                # Local variable — record for inline substitution
                self._locals[stmt.targets[0].id] = stmt.value
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and self._is_self_target(stmt.targets[0]):
                t = self._expr(stmt.targets[0])
                v = self._expr(stmt.value)
                lines.append(f'{pad}{t} {assign_op} {v};')
            elif isinstance(stmt, ast.Expr):
                # Check for mem.write(addr, data) calls
                if isinstance(stmt.value, ast.Call):
                    v = self._emit_mem_write(stmt.value, assign_op)
                    if v:
                        lines.append(f'{pad}{v}')
                        continue
                # standalone expression — skip (e.g. docstrings)
        return lines

    def _emit_if(self, node, indent, assign_op='<='):
        pad = '    ' * indent
        cond = self._expr(node.test)
        lines = [f'{pad}if ({cond}) begin']
        lines += self._stmts_to_v(node.body, indent + 1, assign_op)
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                lines.append(f'{pad}end else')
                lines += self._emit_if(node.orelse[0], indent, assign_op)
            else:
                lines.append(f'{pad}end else begin')
                lines += self._stmts_to_v(node.orelse, indent + 1, assign_op)
                lines.append(f'{pad}end')
        else:
            lines.append(f'{pad}end')
        return lines

    def _try_case_chain(self, node):
        """Detect if/elif/else chain comparing one signal against constants.

        Returns (signal_expr, [(const_expr, body), ...], default_body) or None.
        """
        signal = None
        branches = []
        cur = node
        while isinstance(cur, ast.If):
            t = cur.test
            if not (isinstance(t, ast.Compare) and len(t.ops) == 1
                    and isinstance(t.ops[0], ast.Eq)):
                return None
            lhs, rhs = t.left, t.comparators[0]
            # Determine which side is the signal and which is the constant
            if self._is_self_target(lhs) or (isinstance(lhs, ast.Attribute) and self._is_self(lhs.value)):
                sig_node, const_node = lhs, rhs
            elif self._is_self_target(rhs) or (isinstance(rhs, ast.Attribute) and self._is_self(rhs.value)):
                sig_node, const_node = rhs, lhs
            else:
                return None
            sig_str = self._expr(sig_node)
            if signal is None:
                signal = sig_str
            elif sig_str != signal:
                return None
            branches.append((const_node, cur.body))
            # Walk the else chain
            if not cur.orelse:
                return (signal, branches, None)
            if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                cur = cur.orelse[0]
            else:
                return (signal, branches, cur.orelse)
        return (signal, branches, None)

    def _emit_case(self, case_info, indent, assign_op):
        signal, branches, default = case_info
        pad = '    ' * indent
        lines = [f'{pad}case ({signal})']
        for const_node, body in branches:
            label = self._expr(const_node)
            lines.append(f'{pad}    {label}: begin')
            lines += self._stmts_to_v(body, indent + 2, assign_op)
            lines.append(f'{pad}    end')
        if default:
            lines.append(f'{pad}    default: begin')
            lines += self._stmts_to_v(default, indent + 2, assign_op)
            lines.append(f'{pad}    end')
        lines.append(f'{pad}endcase')
        return lines

    def _emit_for(self, node, indent, assign_op='<='):
        """Unroll for-loops with constant range, substituting the loop variable."""
        if not (isinstance(node.iter, ast.Call) and
                isinstance(node.iter.func, ast.Name) and
                node.iter.func.id == 'range'):
            raise SyntaxError('Only for ... in range(...) is supported')
        args = [self._const_eval(a) for a in node.iter.args]
        if len(args) == 1:
            start, stop, step = 0, args[0], 1
        elif len(args) == 2:
            start, stop, step = args[0], args[1], 1
        else:
            start, stop, step = args[0], args[1], args[2]
        var_name = node.target.id
        lines = []
        for val in range(start, stop, step):
            body = self._subst_var(node.body, var_name, val)
            lines += self._stmts_to_v(body, indent, assign_op)
        return lines

    def _subst_var(self, stmts, var_name, val):
        """Replace Name(var_name) with Constant(val), then constant-fold BinOps."""
        import copy
        stmts = copy.deepcopy(stmts)
        for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
            for field, child in ast.iter_fields(node):
                if isinstance(child, ast.Name) and child.id == var_name:
                    setattr(node, field, ast.Constant(value=val))
                elif isinstance(child, list):
                    for i, item in enumerate(child):
                        if isinstance(item, ast.Name) and item.id == var_name:
                            child[i] = ast.Constant(value=val)
        for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
            for field, child in ast.iter_fields(node):
                if isinstance(child, ast.BinOp):
                    folded = self._try_fold(child)
                    if folded is not None:
                        setattr(node, field, folded)
                elif isinstance(child, list):
                    for i, item in enumerate(child):
                        if isinstance(item, ast.BinOp):
                            folded = self._try_fold(item)
                            if folded is not None:
                                child[i] = folded
        return stmts

    def _try_fold(self, node):
        if not (isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant)):
            return None
        l, r = node.left.value, node.right.value
        if isinstance(node.op, ast.Add): return ast.Constant(value=l + r)
        if isinstance(node.op, ast.Sub): return ast.Constant(value=l - r)
        if isinstance(node.op, ast.Mult): return ast.Constant(value=l * r)
        return None

    # --- expression translation ---
    def _expr(self, node):
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return "1'b1" if v else "1'b0"
            return str(v)

        if isinstance(node, ast.Name):
            # Check inline-substituted local variables first
            if node.id in self._locals:
                return self._expr(self._locals[node.id])
            if self._current_func:
                try:
                    val = self._resolve_name(node.id)
                    if isinstance(val, _ParamRef):
                        return val.name
                    return str(val)
                except SyntaxError:
                    pass
            return node.id

        # self.sub.port → sub_port (sub-module port reference)
        if (isinstance(node, ast.Attribute) and
            isinstance(node.value, ast.Attribute) and
            self._is_self(node.value.value)):
            sub_name = node.value.attr
            if sub_name in self.submodules:
                return f'{sub_name}_{node.attr}'

        # self.foo → foo
        if isinstance(node, ast.Attribute) and self._is_self(node.value):
            return node.attr

        if isinstance(node, ast.BinOp):
            # Try constant folding, but skip if a param is involved
            try:
                l_val = self._const_eval(node.left)
                r_val = self._const_eval(node.right)
                l_param = isinstance(node.left, ast.Name) and self._is_param_name(node.left.id)
                r_param = isinstance(node.right, ast.Name) and self._is_param_name(node.right.id)
                if not l_param and not r_param:
                    ops = {ast.Add: lambda a,b: a+b, ast.Sub: lambda a,b: a-b,
                           ast.Mult: lambda a,b: a*b}
                    fn = ops.get(type(node.op))
                    if fn is not None:
                        return str(fn(l_val, r_val))
            except SyntaxError:
                pass
            l, r = self._expr(node.left), self._expr(node.right)
            op = _BIN_OPS.get(type(node.op))
            if op:
                return f'({l} {op} {r})'

        if isinstance(node, ast.BoolOp):
            op = _BOOL_OPS[type(node.op)]
            parts = []
            for v in node.values:
                e = self._expr(v)
                # Parenthesize lower-precedence or inside and
                if isinstance(node.op, ast.And) and isinstance(v, ast.BoolOp) and isinstance(v.op, ast.Or):
                    e = f'({e})'
                parts.append(e)
            return f' {op} '.join(parts)

        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op:
                return f'{op}{self._expr(node.operand)}'

        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            l = self._expr(node.left)
            r = self._expr(node.comparators[0])
            op = _CMP_OPS.get(type(node.ops[0]))
            if op:
                return f'({l} {op} {r})'

        if isinstance(node, ast.Subscript):
            val = self._expr(node.value)
            sl = node.slice
            if isinstance(sl, ast.Slice):
                hi = self._expr(sl.lower) if sl.lower else ''
                lo = self._expr(sl.upper) if sl.upper else '0'
                return f'{val}[{hi}:{lo}]'
            return f'{val}[{self._expr(sl)}]'

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == 'Mux' and len(node.args) == 3:
                sel, a, b = [self._expr(x) for x in node.args]
                return f'({sel}) ? {a} : {b}'
            if isinstance(func, ast.Name) and func.id == 'Cat':
                parts = [self._expr(a) for a in reversed(node.args)]
                return '{' + ', '.join(parts) + '}'

        if isinstance(node, ast.List):
            parts = [self._expr(e) for e in reversed(node.elts)]
            return '{' + ', '.join(parts) + '}'

        if isinstance(node, ast.IfExp):
            cond = self._expr(node.test)
            a = self._expr(node.body)
            b = self._expr(node.orelse)
            return f'(({cond}) ? {a} : {b})'

        raise SyntaxError(f'Unsupported expression: {ast.dump(node)}')

    # --- helpers ---
    def _is_nba(self, stmt):
        return (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and
                self._is_self_target(stmt.targets[0]))

    def _emit_mem_write(self, call_node, assign_op):
        """Detect self.mem.write(addr, data) → mem[addr] <= data;"""
        func = call_node.func
        if (isinstance(func, ast.Attribute) and func.attr == 'write' and
            isinstance(func.value, ast.Attribute) and self._is_self(func.value.value)):
            mem_name = func.value.attr
            if mem_name in self.mems and len(call_node.args) == 2:
                addr = self._expr(call_node.args[0])
                data = self._expr(call_node.args[1])
                return f'{mem_name}[{addr}] {assign_op} {data};'
        return None

    def _extract_nba(self, stmt):
        return self._expr(stmt.targets[0]), self._expr(stmt.value)

    def _is_self(self, node):
        return isinstance(node, ast.Name) and node.id == 'self'

    def _is_self_target(self, node):
        """Check if an assignment target is rooted in self (self.x, self.sub.port, self.x[slice])."""
        if isinstance(node, ast.Subscript):
            return self._is_self_target(node.value)
        if isinstance(node, ast.Attribute):
            return self._is_self(node.value) or self._is_self_target(node.value)
        return False

    def _is_param_name(self, name):
        """Check if a Python variable name is a declared parameter."""
        return name in self.mod._params

    def _resolve_name(self, name):
        func = self._current_func
        if func and hasattr(func, '__code__'):
            code = func.__code__
            # Check closure variables
            if name in code.co_freevars and func.__closure__:
                idx = code.co_freevars.index(name)
                val = func.__closure__[idx].cell_contents
                if name in self.mod._params:
                    return _ParamRef(name, val)
                return val
            # Check globals (module-level constants)
            if hasattr(func, '__globals__') and name in func.__globals__:
                val = func.__globals__[name]
                if isinstance(val, (int, float)):
                    return val
        raise SyntaxError(f'Cannot resolve variable: {name}')

    def _const_eval(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            val = self._resolve_name(node.id)
            if isinstance(val, _ParamRef):
                return val.value  # use concrete value for const eval
            return val
        if isinstance(node, ast.BinOp):
            l = self._const_eval(node.left)
            r = self._const_eval(node.right)
            if type(node.op) == ast.Add: return l + r
            if type(node.op) == ast.Sub: return l - r
            if type(node.op) == ast.Mult: return l * r
        raise SyntaxError(f'Expected constant, got {ast.dump(node)}')

    def _get_func_ast(self, func):
        src = inspect.getsource(func)
        src = textwrap.dedent(src)
        tree = ast.parse(src)
        func_def = tree.body[0]
        if isinstance(func_def, ast.FunctionDef):
            return func_def
        return tree


class _ParamRef:
    """A reference to a Verilog parameter — str() gives the name, int() gives the value."""
    def __init__(self, name, value):
        self.name = name
        self.value = value
    def __str__(self):
        return self.name
    def __int__(self):
        return int(self.value)
    def __add__(self, o): return int(self) + int(o)
    def __radd__(self, o): return int(o) + int(self)
    def __sub__(self, o): return int(self) - int(o)
    def __rsub__(self, o): return int(o) - int(self)
    def __mul__(self, o): return int(self) * int(o)
