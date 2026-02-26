"""Verilog emitter: parses Python AST of @posedge/@comb methods and emits Verilog."""

import ast
import inspect
import textwrap

from .signal import Signal, SignalArray


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
        self.signals = {}
        self.arrays = {}
        self.submodules = {}
        for k in dir(module):
            v = getattr(module, k)
            if isinstance(v, SignalArray):
                self.arrays[k] = v
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
        for name, arr in sorted(self.arrays.items()):
            w = self._width_str(arr[0])
            lines.append(f'    reg {w}{name} [0:{arr.depth - 1}];')
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
        ports = []
        for name, sig in sorted(self.signals.items()):
            if sig._kind == 'input':
                ports.append((name, sig, 'input'))
        for name, sig in sorted(self.signals.items()):
            if sig._kind == 'output':
                ports.append((name, sig, 'output reg'))
        return ports

    def _width_str(self, sig):
        if sig.width == 1:
            return ''
        return f'[{sig.width - 1}:0] '

    # --- comb blocks → assign or always @(*) ---
    def _emit_comb(self, method):
        tree = self._get_func_ast(method)
        # Simple: all statements are bare <<= → use assign
        if all(self._is_nba(s) for s in tree.body):
            self._current_func = method
            lines = []
            for stmt in tree.body:
                t, v = self._extract_nba(stmt)
                lines.append(f'    assign {t} = {v};')
            lines.append('')
            self._current_func = None
            return lines
        # Complex: has control flow → always @(*)
        self._current_func = method
        lines = ['    always @(*) begin']
        lines += self._stmts_to_v(tree.body, indent=2, assign_op='=')
        lines += ['    end', '']
        self._current_func = None
        return lines

    # --- posedge blocks → always @(posedge clk) ---
    def _emit_posedge(self, clk, method):
        tree = self._get_func_ast(method)
        self._current_func = method
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
                lines += self._emit_if(stmt, indent, assign_op)
            elif isinstance(stmt, ast.For):
                lines += self._emit_for(stmt, indent, assign_op)
            elif self._is_nba(stmt):
                t, v = self._extract_nba(stmt)
                lines.append(f'{pad}{t} {assign_op} {v};')
            elif isinstance(stmt, ast.Expr):
                pass
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
            if self._current_func:
                try:
                    return str(self._resolve_name(node.id))
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
            try:
                l_val = self._const_eval(node.left)
                r_val = self._const_eval(node.right)
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
            parts = [self._expr(v) for v in node.values]
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
            return f'({cond}) ? {a} : {b}'

        raise SyntaxError(f'Unsupported expression: {ast.dump(node)}')

    # --- helpers ---
    def _is_nba(self, stmt):
        return (isinstance(stmt, ast.AugAssign) and
                isinstance(stmt.op, ast.LShift))

    def _extract_nba(self, stmt):
        return self._expr(stmt.target), self._expr(stmt.value)

    def _is_self(self, node):
        return isinstance(node, ast.Name) and node.id == 'self'

    def _resolve_name(self, name):
        func = self._current_func
        if func and hasattr(func, '__code__') and hasattr(func, '__closure__'):
            code = func.__code__
            if name in code.co_freevars and func.__closure__:
                idx = code.co_freevars.index(name)
                return func.__closure__[idx].cell_contents
        raise SyntaxError(f'Cannot resolve variable: {name}')

    def _const_eval(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
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
