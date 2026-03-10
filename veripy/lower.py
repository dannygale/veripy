"""Lowering pass: Module → IRModule.

Resolves all Python names, folds constants, classifies locals,
detects case chains, and produces a clean IR with no Python AST references.
"""

import ast
import inspect
import textwrap

from .ir import (
    Expr, Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat, Clz, Ctz, Popcount, Sext,
    Stmt, Assign, SliceAssign, If, Case, MemWrite, Delay, Display, Finish,
    Repeat, ForLoop, Disable,
    ContAssign, CombBlock, SeqBlock, InitialBlock, AlwaysBlock,
    Port, WireDecl, RegDecl, MemDecl, DualPortMemDecl, TrueDualPortMemDecl,
    Instance, IRModule, FormalProperty,
)
from .signal import Signal, Mem, DualPortMem, TrueDualPortMem, Interface
from .parameter import is_param, ParamExpr, Parameter

_BIN_OPS = {
    ast.Add: '+', ast.Sub: '-', ast.Mult: '*', ast.FloorDiv: '/',
    ast.Mod: '%', ast.BitAnd: '&', ast.BitOr: '|', ast.BitXor: '^',
    ast.LShift: '<<', ast.RShift: '>>',
}
_UNARY_OPS = {ast.Invert: '~', ast.Not: '!', ast.USub: '-'}
_CMP_OPS = {
    ast.Eq: '==', ast.NotEq: '!=', ast.Lt: '<', ast.Gt: '>',
    ast.LtE: '<=', ast.GtE: '>=',
}
_BOOL_OPS = {ast.And: '&&', ast.Or: '||'}


def _width_str(sig):
    """Return width as int or param expression string."""
    if sig._width_param is not None:
        return sig._width_param.name
    return sig.width


def _to_snake(name):
    import re
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).lower()


class _Lowerer:
    """Lowers Python AST blocks into IR, with full name resolution."""

    def __init__(self, signals, mems, submodules, interfaces, params, module=None, port_arrays=None):
        self.signals = signals
        self.mems = mems
        self.submodules = submodules
        self.interfaces = interfaces
        self.params = params
        self._module = module
        self.port_arrays = port_arrays or {}  # name → [sig_name_0, sig_name_1, ...]
        self._func = None          # current function being lowered
        self._reg_locals = {}      # name → width for Register locals
        self._all_reg_locals = {}  # accumulated across blocks

    # ── Name resolution ──────────────────────────────────────────────

    def _resolve_name(self, name):
        """Resolve a bare Python name to its value via closure/globals."""
        func = self._func
        if func and hasattr(func, '__code__'):
            code = func.__code__
            if name in code.co_freevars and func.__closure__:
                idx = code.co_freevars.index(name)
                val = func.__closure__[idx].cell_contents
                if name in self.params:
                    return ('param', name, val)
                if isinstance(val, ParamExpr):
                    return ('param', val.name, val)
                if isinstance(val, Parameter) and val.name:
                    return ('param', val.name, val)
                if isinstance(val, (int, float)):
                    return ('const', val)
                return ('obj', val)
            if hasattr(func, '__globals__') and name in func.__globals__:
                val = func.__globals__[name]
                if isinstance(val, (int, float)):
                    return ('const', val)
                return ('obj', val)
        raise SyntaxError(f'Cannot resolve: {name}')

    def _is_self(self, node):
        return isinstance(node, ast.Name) and node.id == 'self'

    def _resolve_closure(self, name):
        """Look up a name in the current function's closure/globals."""
        func = self._func
        if func and hasattr(func, '__code__') and func.__closure__:
            for i, n in enumerate(func.__code__.co_freevars):
                if n == name:
                    return func.__closure__[i].cell_contents
        if func and hasattr(func, '__globals__') and name in func.__globals__:
            return func.__globals__[name]
        return None

    def _is_self_target(self, node):
        if isinstance(node, ast.Subscript):
            return self._is_self_target(node.value)
        if isinstance(node, ast.Attribute):
            return self._is_self(node.value) or self._is_self_target(node.value)
        return False

    def _target_name(self, node):
        """Extract flat signal name from self.x, self.sub.port, self.sub.iface.sig."""
        if (isinstance(node, ast.Attribute) and
            isinstance(node.value, ast.Attribute) and
            isinstance(node.value.value, ast.Attribute) and
            self._is_self(node.value.value.value)):
            sub = node.value.value.attr
            iface = node.value.attr
            if sub in self.submodules:
                return f'{sub}_{iface}_{node.attr}'
        if (isinstance(node, ast.Attribute) and
            isinstance(node.value, ast.Attribute) and
            self._is_self(node.value.value)):
            sub = node.value.attr
            if sub in self.submodules or sub in self.interfaces:
                return f'{sub}_{node.attr}'
        if isinstance(node, ast.Attribute) and self._is_self(node.value):
            return node.attr
        return None

    # ── Constant evaluation ──────────────────────────────────────────

    def _const_eval(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            r = self._resolve_name(node.id)
            if r[0] == 'param':
                return r[2]  # concrete value
            if r[0] == 'const':
                return r[1]
        if isinstance(node, ast.BinOp):
            l = self._const_eval(node.left)
            r = self._const_eval(node.right)
            ops = {ast.Add: lambda a,b: a+b, ast.Sub: lambda a,b: a-b,
                   ast.Mult: lambda a,b: a*b, ast.FloorDiv: lambda a,b: a//b}
            fn = ops.get(type(node.op))
            if fn:
                return fn(l, r)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._const_eval(node.operand)
        raise SyntaxError(f'Not constant: {ast.dump(node)}')

    # ── Expression lowering ──────────────────────────────────────────

    def _expr(self, node):
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return Const(1 if v else 0)
            return Const(v)

        if isinstance(node, ast.Name):
            name = node.id
            # Register locals
            if name in self._reg_locals:
                return Sig(name)
            # Try to resolve via closure/globals
            try:
                r = self._resolve_name(name)
                if r[0] == 'param':
                    return Param(r[1])
                if r[0] == 'const':
                    return Const(r[1])
                if r[0] == 'obj' and isinstance(r[1], Signal):
                    return Sig(r[1].name)
            except SyntaxError:
                pass
            return Sig(name)

        # self.sub.iface.signal
        if (isinstance(node, ast.Attribute) and
            isinstance(node.value, ast.Attribute) and
            isinstance(node.value.value, ast.Attribute) and
            self._is_self(node.value.value.value)):
            sub = node.value.value.attr
            iface = node.value.attr
            if sub in self.submodules:
                return Sig(f'{sub}_{iface}_{node.attr}')

        # self.sub.port
        if (isinstance(node, ast.Attribute) and
            isinstance(node.value, ast.Attribute) and
            self._is_self(node.value.value)):
            sub = node.value.attr
            if sub in self.submodules or sub in self.interfaces:
                return Sig(f'{sub}_{node.attr}')

        # self.foo
        if isinstance(node, ast.Attribute) and self._is_self(node.value):
            return Sig(node.attr)

        # Closure attribute access (e.g. pipe.result → _pipe_stage1)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            try:
                r = self._resolve_name(node.value.id)
                if r[0] == 'obj':
                    val = getattr(r[1], node.attr, None)
                    if isinstance(val, Signal):
                        return Sig(val.name)
                elif r[0] == 'const' or r[0] == 'param':
                    pass
            except SyntaxError:
                pass

        if isinstance(node, ast.BinOp):
            # Try constant folding (skip if params involved)
            try:
                l_val = self._const_eval(node.left)
                r_val = self._const_eval(node.right)
                l_param = isinstance(node.left, ast.Name) and node.left.id in self.params
                r_param = isinstance(node.right, ast.Name) and node.right.id in self.params
                if not l_param and not r_param:
                    ops = {ast.Add: lambda a,b: a+b, ast.Sub: lambda a,b: a-b,
                           ast.Mult: lambda a,b: a*b, ast.FloorDiv: lambda a,b: a//b}
                    fn = ops.get(type(node.op))
                    if fn:
                        return Const(fn(l_val, r_val))
            except SyntaxError:
                pass
            op = _BIN_OPS.get(type(node.op))
            if op:
                return BinOp(op, self._expr(node.left), self._expr(node.right))

        if isinstance(node, ast.BoolOp):
            op = _BOOL_OPS[type(node.op)]
            return BoolOp(op, [self._expr(v) for v in node.values])

        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op:
                return UnaryOp(op, self._expr(node.operand))

        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = _CMP_OPS.get(type(node.ops[0]))
            if op:
                return Compare(op, self._expr(node.left), self._expr(node.comparators[0]))

        if isinstance(node, ast.Subscript):
            # self.port_array[i] → Sig('port_array_i') when i is a constant
            if (isinstance(node.value, ast.Attribute) and self._is_self(node.value.value)):
                attr = node.value.attr
                if attr in self.port_arrays:
                    try:
                        idx = self._const_eval(node.slice)
                        return Sig(self.port_arrays[attr][idx])
                    except Exception:
                        pass
            val = self._expr(node.value)
            sl = node.slice
            if isinstance(sl, ast.Slice):
                hi = self._expr(sl.lower) if sl.lower else None
                lo = self._expr(sl.upper) if sl.upper else Const(0)
                return Slice(val, hi, lo)
            return Index(val, self._expr(sl))

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == 'Mux' and len(node.args) == 3:
                return Mux(self._expr(node.args[0]), self._expr(node.args[1]), self._expr(node.args[2]))
            if isinstance(func, ast.Name) and func.id == 'Cat':
                return Concat([self._expr(a) for a in reversed(node.args)])
            # int(expr) → just the inner expr
            if isinstance(func, ast.Name) and func.id == 'int' and len(node.args) == 1:
                return self._expr(node.args[0])
            # Built-in bit primitives: clz, ctz, popcount, sext
            if isinstance(func, ast.Name) and func.id in ('clz', 'ctz', 'popcount') and len(node.args) == 1:
                operand = self._expr(node.args[0])
                w = self._signal_width(operand)
                cls = {'clz': Clz, 'ctz': Ctz, 'popcount': Popcount}[func.id]
                return cls(operand, w)
            if isinstance(func, ast.Name) and func.id == 'sext' and len(node.args) == 2:
                operand = self._expr(node.args[0])
                src_w = self._signal_width(operand)
                dst_w = self._const_eval(node.args[1])
                return Sext(operand, src_w, dst_w)

        if isinstance(node, ast.List):
            return Concat([self._expr(e) for e in reversed(node.elts)])

        if isinstance(node, ast.IfExp):
            return Mux(self._expr(node.test), self._expr(node.body), self._expr(node.orelse))

        raise SyntaxError(f'Unsupported expression: {ast.dump(node)}')

    # ── Statement lowering ───────────────────────────────────────────

    def _stmts(self, stmts, blocking=True):
        out = []
        for stmt in stmts:
            out.extend(self._stmt(stmt, blocking))
        return out

    def _stmt(self, stmt, blocking=True):
        if isinstance(stmt, ast.If):
            case = self._try_case(stmt, blocking)
            if case:
                return [case]
            return [If(
                self._expr(stmt.test),
                self._stmts(stmt.body, blocking),
                self._stmts(stmt.orelse, blocking) if stmt.orelse else [],
            )]

        if isinstance(stmt, ast.For):
            return self._lower_for(stmt, blocking)

        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]

            # Local variable (Register or plain)
            if isinstance(target, ast.Name):
                name = target.id
                if name in self._reg_locals:
                    # Skip Register(...) declarations
                    if (isinstance(stmt.value, ast.Call) and
                        isinstance(stmt.value.func, ast.Name) and
                        stmt.value.func.id == 'Register'):
                        return []
                    return [Assign(name, self._expr(stmt.value), blocking=True)]
                # Bare name that matches a known signal → treat as signal assign
                if name in self.signals:
                    return [Assign(name, self._expr(stmt.value), blocking)]
                # Plain local — treat as reg
                if name not in self._reg_locals:
                    w = self._infer_rhs_width(stmt.value)
                    self._reg_locals[name] = w if w is not None else 32
                return [Assign(name, self._expr(stmt.value), blocking=True)]

            # self.x[slice] = val
            if isinstance(target, ast.Subscript) and self._is_self_target(target):
                tname = self._target_name(target.value)
                # Port array assignment: self.port_array[i] = val → Assign('port_array_i', val)
                if tname in self.port_arrays:
                    try:
                        idx = self._const_eval(target.slice)
                        return [Assign(self.port_arrays[tname][idx], self._expr(stmt.value), blocking)]
                    except Exception:
                        pass
                sl = target.slice
                if isinstance(sl, ast.Slice):
                    hi = self._expr(sl.lower) if sl.lower else None
                    lo = self._expr(sl.upper) if sl.upper else Const(0)
                    return [SliceAssign(tname, hi, lo, self._expr(stmt.value), blocking)]
                return [SliceAssign(tname, self._expr(sl), self._expr(sl), self._expr(stmt.value), blocking)]

            # self.x = val (signal assign)
            if self._is_self_target(target):
                tname = self._target_name(target)
                if tname:
                    return [Assign(tname, self._expr(stmt.value), blocking)]

        # Augmented assign: self.x += val
        if isinstance(stmt, ast.AugAssign) and self._is_self_target(stmt.target):
            tname = self._target_name(stmt.target)
            op = _BIN_OPS.get(type(stmt.op))
            if tname and op:
                return [Assign(tname, BinOp(op, Sig(tname), self._expr(stmt.value)), blocking)]

        # mem.write(addr, data)
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            func = call.func
            if (isinstance(func, ast.Attribute) and func.attr == 'write' and
                isinstance(func.value, ast.Attribute) and self._is_self(func.value.value)):
                mem_name = func.value.attr
                if mem_name in self.mems and len(call.args) == 2:
                    return [MemWrite(mem_name, self._expr(call.args[0]),
                                     self._expr(call.args[1]), blocking)]

            # closure_var._assign(val) — e.g. state_reg._assign(0)
            if (isinstance(func, ast.Attribute) and func.attr == '_assign'
                    and isinstance(func.value, ast.Name) and len(call.args) == 1):
                sig = self._resolve_closure(func.value.id)
                if isinstance(sig, Signal):
                    return [Assign(sig.name, self._expr(call.args[0]), blocking)]

        return []  # skip unrecognized (docstrings, etc.)
        # Warn on silently dropped statements (skip docstrings and pass)
        if isinstance(stmt, ast.Pass):
            return []
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            return []  # docstring
        import warnings
        loc = f' (line {stmt.lineno})' if hasattr(stmt, 'lineno') else ''
        warnings.warn(f'@comb/@always: unsupported statement dropped{loc}: {ast.dump(stmt)[:80]}',
                      stacklevel=2)
        return []

    # ── Case detection ───────────────────────────────────────────────

    def _try_case(self, node, blocking):
        """Detect if/elif chain comparing one signal against constants → Case."""
        signal_expr = None
        branches = []
        cur = node
        while isinstance(cur, ast.If):
            t = cur.test
            if not (isinstance(t, ast.Compare) and len(t.ops) == 1
                    and isinstance(t.ops[0], ast.Eq)):
                return None
            lhs, rhs = t.left, t.comparators[0]
            if self._is_self_target(lhs) or (isinstance(lhs, ast.Attribute) and self._is_self(lhs.value)):
                sig_node, const_node = lhs, rhs
            elif self._is_self_target(rhs) or (isinstance(rhs, ast.Attribute) and self._is_self(rhs.value)):
                sig_node, const_node = rhs, lhs
            else:
                return None
            sig = self._expr(sig_node)
            if signal_expr is None:
                signal_expr = sig
            elif sig != signal_expr:
                return None
            branches.append((self._expr(const_node), self._stmts(cur.body, blocking)))
            if not cur.orelse:
                return Case(signal_expr, branches, None)
            if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                cur = cur.orelse[0]
            else:
                return Case(signal_expr, branches, self._stmts(cur.orelse, blocking))
        return Case(signal_expr, branches, None)

    # ── For loop unrolling ───────────────────────────────────────────

    def _resolve_iterable(self, node):
        """Try to resolve an AST node to a Python list/tuple at lowering time."""
        # self.attr
        if isinstance(node, ast.Attribute) and self._is_self(node.value):
            if self._module is not None:
                val = getattr(self._module, node.attr, None)
                if isinstance(val, (list, tuple)):
                    return val
        # bare name from closure/globals
        if isinstance(node, ast.Name):
            val = self._resolve_closure(node.id)
            if isinstance(val, (list, tuple)):
                return val
        return None

    def _lower_for(self, node, blocking):
        # for var in self.port_array: → unroll over each signal
        if (isinstance(node.iter, ast.Attribute) and self._is_self(node.iter.value)):
            attr = node.iter.attr
            if attr in self.port_arrays:
                var = node.target.id
                out = []
                for sig_name in self.port_arrays[attr]:
                    # Replace Name(var) with Attribute(self, sig_name) in body
                    import copy
                    body = copy.deepcopy(node.body)
                    for n in ast.walk(ast.Module(body=body, type_ignores=[])):
                        for field, child in ast.iter_fields(n):
                            if isinstance(child, ast.Name) and child.id == var:
                                setattr(n, field, ast.Attribute(
                                    value=ast.Name(id='self', ctx=ast.Load()),
                                    attr=sig_name, ctx=ast.Load()))
                            elif isinstance(child, list):
                                for i, item in enumerate(child):
                                    if isinstance(item, ast.Name) and item.id == var:
                                        child[i] = ast.Attribute(
                                            value=ast.Name(id='self', ctx=ast.Load()),
                                            attr=sig_name, ctx=ast.Load())
                    ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
                    out.extend(self._stmts(body, blocking))
                return out

        if not (isinstance(node.iter, ast.Call) and
                isinstance(node.iter.func, ast.Name) and
                node.iter.func.id == 'range'):
            # Try to resolve iterable to a Python list/tuple and unroll
            iterable = self._resolve_iterable(node.iter)
            if iterable is None:
                raise SyntaxError('Only for ... in range(...) or a resolvable iterable is supported')
            var = node.target.id
            out = []
            for elem in iterable:
                body = self._subst_var_obj(node.body, var, elem)
                out.extend(self._stmts(body, blocking))
            return out
        args = [self._const_eval(a) for a in node.iter.args]
        if len(args) == 1:
            start, stop, step = 0, args[0], 1
        elif len(args) == 2:
            start, stop, step = args[0], args[1], 1
        else:
            start, stop, step = args
        var = node.target.id
        # Detect break → unroll as elif chain for priority semantics
        has_break = any(isinstance(n, ast.Break)
                        for n in ast.walk(ast.Module(body=node.body, type_ignores=[])))
        if has_break:
            return self._unroll_for_break(node.body, var, start, stop, step, blocking)
        out = []
        for val in range(start, stop, step):
            body = self._subst_var(node.body, var, val)
            out.extend(self._stmts(body, blocking))
        return out

    def _unroll_for_break(self, body_ast, var, start, stop, step, blocking):
        """Unroll a for loop with break into a nested if/elif chain."""
        iterations = list(range(start, stop, step))
        if not iterations:
            return []
        # Build elif chain from last to first (innermost else is empty)
        result = []
        for val in reversed(iterations):
            sub = self._subst_var(body_ast, var, val)
            # Strip break statements from the body
            sub = [s for s in sub if not isinstance(s, ast.Break)]
            # Find the if-break pattern: if cond: stmts; break
            if (len(sub) == 1 and isinstance(sub[0], ast.If)):
                if_node = sub[0]
                # Strip breaks from the if body
                then_body = [s for s in if_node.body if not isinstance(s, ast.Break)]
                cond = self._expr(if_node.test)
                then_stmts = self._stmts(then_body, blocking)
                result = [If(cond, then_stmts, result)]
            else:
                # General case: lower the body, wrap in elif
                stmts = self._stmts(sub, blocking)
                if result:
                    # Can't easily chain — emit as flat with a guard local
                    # Fall back to flat unroll (break has no effect)
                    return self._stmts(sub, blocking) + result
                result = stmts
        return result

    def _subst_var_obj(self, stmts, var_name, obj):
        """AST-level substitution of *var_name* with a Python object.

        Handles ``var.attr`` (attribute access on the loop variable) and bare
        ``var`` references.  Signals become ``self.sig_name`` AST nodes;
        ints become ``ast.Constant``.
        """
        import copy
        stmts = copy.deepcopy(stmts)

        def _replacement(val):
            if isinstance(val, Signal):
                return ast.Attribute(
                    value=ast.Name(id='self', ctx=ast.Load()),
                    attr=val.name, ctx=ast.Load())
            if isinstance(val, (int, float)):
                return ast.Constant(value=val)
            return None

        for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
            for field, child in ast.iter_fields(node):
                if isinstance(child, list):
                    for i, item in enumerate(child):
                        if (isinstance(item, ast.Attribute)
                                and isinstance(item.value, ast.Name)
                                and item.value.id == var_name):
                            r = _replacement(getattr(obj, item.attr, None))
                            if r is not None:
                                child[i] = r
                        elif isinstance(item, ast.Name) and item.id == var_name:
                            r = _replacement(obj)
                            if r is not None:
                                child[i] = r
                elif (isinstance(child, ast.Attribute)
                      and isinstance(child.value, ast.Name)
                      and child.value.id == var_name):
                    r = _replacement(getattr(obj, child.attr, None))
                    if r is not None:
                        setattr(node, field, r)
                elif isinstance(child, ast.Name) and child.id == var_name:
                    r = _replacement(obj)
                    if r is not None:
                        setattr(node, field, r)

        ast.fix_missing_locations(ast.Module(body=stmts, type_ignores=[]))
        return stmts

    def _subst_var(self, stmts, var_name, val):
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
        # Fold constants
        for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
            for field, child in ast.iter_fields(node):
                if isinstance(child, ast.BinOp):
                    f = self._try_fold_ast(child)
                    if f is not None:
                        setattr(node, field, f)
                elif isinstance(child, list):
                    for i, item in enumerate(child):
                        if isinstance(item, ast.BinOp):
                            f = self._try_fold_ast(item)
                            if f is not None:
                                child[i] = f
        return stmts

    def _try_fold_ast(self, node):
        if isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant):
            l, r = node.left.value, node.right.value
            ops = {ast.Add: lambda a,b: a+b, ast.Sub: lambda a,b: a-b,
                   ast.Mult: lambda a,b: a*b, ast.FloorDiv: lambda a,b: a//b}
            fn = ops.get(type(node.op))
            if fn:
                return ast.Constant(value=fn(l, r))
        return None

    # ── Scan for Register locals ─────────────────────────────────────

    def _signal_width(self, ir_expr):
        """Get the width of an IR expression from known signal widths."""
        if isinstance(ir_expr, Sig) and ir_expr.name in self.signals:
            s = self.signals[ir_expr.name]
            return s.width if hasattr(s, 'width') else getattr(s, '_width', 64)
        if isinstance(ir_expr, Slice):
            if ir_expr.hi is None:
                return 1
            if isinstance(ir_expr.hi, Const) and isinstance(ir_expr.lo, Const):
                return ir_expr.hi.value - ir_expr.lo.value + 1
        if isinstance(ir_expr, Index):
            return 1
        # Default: try to find width from the widest signal in the expression
        return 64

    def _infer_rhs_width(self, node):
        """Try to evaluate an AST expression to get its width."""
        try:
            func = self._func
            ns = dict(func.__globals__) if hasattr(func, '__globals__') else {}
            if hasattr(func, '__code__') and func.__closure__:
                for n, cell in zip(func.__code__.co_freevars, func.__closure__):
                    ns[n] = cell.cell_contents
            if self._module is not None:
                ns['self'] = self._module
            val = eval(compile(ast.Expression(body=node), '<width>', 'eval'), ns)
            return getattr(val, '_width', None) or getattr(val, 'width', None)
        except Exception:
            return self._infer_width_from_signals(node)

    def _infer_width_from_signals(self, node):
        """Fallback: find the widest signal referenced in an AST expression."""
        max_w = 0
        for child in ast.walk(node):
            name = None
            if isinstance(child, ast.Attribute) and self._is_self(child.value):
                name = child.attr
            elif isinstance(child, ast.Name) and child.id in self.signals:
                name = child.id
            elif isinstance(child, ast.Name):
                try:
                    r = self._resolve_name(child.id)
                    if r[0] == 'obj' and isinstance(r[1], Signal):
                        w = r[1].width
                        if isinstance(w, int) and w > max_w:
                            max_w = w
                        continue
                except (SyntaxError, AttributeError):
                    pass
                continue
            if name and name in self.signals:
                sig = self.signals[name]
                w = sig.width if hasattr(sig, 'width') else getattr(sig, '_width', 0)
                if isinstance(w, int) and w > max_w:
                    max_w = w
        return max_w if max_w > 0 else None

    def _scan_reg_locals(self, tree):
        regs = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                name = node.targets[0].id
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'Register':
                    arg = node.value.args[0] if node.value.args else None
                    if arg is None:
                        regs[name] = 1
                    elif isinstance(arg, ast.Constant):
                        regs[name] = arg.value
                    elif isinstance(arg, ast.Name):
                        try:
                            r = self._resolve_name(arg.id)
                            if r[0] == 'param':
                                regs[name] = r[1]  # store param name
                            elif r[0] == 'const':
                                regs[name] = r[1]
                        except SyntaxError:
                            regs[name] = 32
                    else:
                        w = self._infer_rhs_width(node.value)
                        regs[name] = w if w is not None else 32
                elif name not in self.signals:
                    w = self._infer_rhs_width(node.value)
                    if w is not None:
                        if isinstance(w, int):
                            existing = regs.get(name, 0)
                            regs[name] = max(existing, w) if isinstance(existing, int) else w
                        else:
                            regs.setdefault(name, w)  # param name string
        return regs

    # ── Get function AST ─────────────────────────────────────────────

    def _get_func_ast(self, func):
        emit_src = getattr(func, '_veripy_emit_source', None)
        src = emit_src if emit_src else textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(src)
        func_def = tree.body[0]
        return func_def if isinstance(func_def, ast.FunctionDef) else tree

    # ── Block lowering ───────────────────────────────────────────────

    def lower_comb(self, method, always_driven):
        """Lower a @comb block → list of ContAssign and/or CombBlock."""
        self._func = method
        self._reg_locals = {}

        # FSM comb blocks need special handling
        fsm_info = getattr(method, '_fsm_info', None)
        if fsm_info:
            return self._lower_fsm_comb(method, fsm_info, always_driven)

        tree = self._get_func_ast(method)

        # Simple: all statements are bare self.x = expr
        if all(self._is_nba(s) for s in tree.body):
            assigns = []
            comb_stmts = []
            for stmt in tree.body:
                # Interface bulk connect
                expanded = self._try_expand_iface(stmt)
                if expanded is not None:
                    for t, v in expanded:
                        if t in always_driven:
                            comb_stmts.append(Assign(t, v, blocking=True))
                        else:
                            assigns.append(ContAssign(t, v))
                    continue
                tname = self._target_name(stmt.targets[0])
                val = self._expr(stmt.value)
                if tname in always_driven:
                    comb_stmts.append(Assign(tname, val, blocking=True))
                else:
                    assigns.append(ContAssign(tname, val))
            result = list(assigns)
            if comb_stmts:
                result.append(CombBlock(comb_stmts))
            self._func = None
            return result

        # Complex: has control flow → CombBlock
        self._reg_locals = self._scan_reg_locals(tree)
        stmts = self._stmts(tree.body, blocking=True)
        locals_dict = {}
        for name, w in self._reg_locals.items():
            if name not in self._all_reg_locals:
                self._all_reg_locals[name] = w
                locals_dict[name] = w
        self._func = None
        return [CombBlock(stmts, locals_dict)]

    def _is_nba(self, stmt):
        return (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and
                self._is_self_target(stmt.targets[0]))

    def _try_expand_iface(self, stmt):
        """Expand interface bulk connect: self.sub.iface = self.iface → list of (target, value) pairs."""
        if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
            return None
        target, value = stmt.targets[0], stmt.value
        # Check if both sides are interface references
        l_iface = self._resolve_iface(target)
        r_iface = self._resolve_iface(value)
        if l_iface is None or r_iface is None:
            return None
        l_prefix, l_obj = l_iface
        r_prefix, r_obj = r_iface
        pairs = []
        for name in l_obj._signals():
            l_wire = f'{l_prefix}_{name}'
            r_wire = f'{r_prefix}_{name}'
            l_sig = l_obj._signals()[name]
            r_sig = r_obj._signals()[name] if name in r_obj._signals() else None
            if r_sig is None:
                continue
            # Direction-aware: input on left = drive from right
            if l_sig._kind == 'input':
                pairs.append((l_wire, Sig(r_wire)))
            else:
                pairs.append((r_wire, Sig(l_wire)))
        return pairs

    def _resolve_iface(self, node):
        """If node is self.sub.iface or self.iface, return (prefix, Interface)."""
        if (isinstance(node, ast.Attribute) and
            isinstance(node.value, ast.Attribute) and
            self._is_self(node.value.value)):
            sub_name = node.value.attr
            iface_name = node.attr
            sub = self.submodules.get(sub_name)
            if sub:
                obj = getattr(sub, iface_name, None)
                if isinstance(obj, Interface):
                    return (f'{sub_name}_{iface_name}', obj)
        if isinstance(node, ast.Attribute) and self._is_self(node.value):
            obj = self.interfaces.get(node.attr)
            if obj:
                return (node.attr, obj)
        return None

    def _lower_fsm_comb(self, method, fsm_info, always_driven):
        """Lower an FSM comb block: state param → _fsm_state, return → _fsm_next."""
        wrapped = getattr(method, '__wrapped__', method)
        self._func = wrapped  # use wrapped func for name resolution (has state globals)
        tree = self._get_func_ast(wrapped)

        # The function has a 'state' parameter — map it to _fsm_state
        state_param = tree.args.args[0].arg if tree.args.args else 'state'
        state_vals = fsm_info['state_vals']

        # Rewrite AST: replace state param refs with _fsm_state,
        # resolve state constants, convert return → _fsm_next assignment
        import copy
        body = copy.deepcopy(tree.body)
        self._fsm_rewrite(body, state_param, state_vals)

        self._reg_locals = {}
        stmts = self._stmts(body, blocking=True)
        # Add default: _fsm_next = _fsm_state
        stmts.insert(0, Assign('_fsm_next', Sig('_fsm_state'), blocking=True))
        self._func = None
        return [CombBlock(stmts)]

    def _fsm_rewrite(self, nodes, state_param, state_vals):
        """In-place AST rewrite for FSM: state param → self._fsm_state,
        state names → constants, return X → self._fsm_next = X."""
        for i, node in enumerate(nodes):
            if isinstance(node, ast.Return):
                # return X → self._fsm_next = X
                val = node.value if node.value else ast.Name(id=state_param, ctx=ast.Load())
                nodes[i] = ast.Assign(
                    targets=[ast.Attribute(
                        value=ast.Name(id='self', ctx=ast.Load()),
                        attr='_fsm_next', ctx=ast.Store())],
                    value=val)
                self._fsm_rewrite_expr(nodes[i].value, state_param, state_vals)
            elif isinstance(node, ast.If):
                self._fsm_rewrite_expr(node.test, state_param, state_vals)
                self._fsm_rewrite(node.body, state_param, state_vals)
                self._fsm_rewrite(node.orelse, state_param, state_vals)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    self._fsm_rewrite_expr(t, state_param, state_vals)
                self._fsm_rewrite_expr(node.value, state_param, state_vals)
            elif isinstance(node, ast.Expr):
                self._fsm_rewrite_expr(node.value, state_param, state_vals)

    def _fsm_rewrite_expr(self, node, state_param, state_vals):
        """Rewrite Name refs: state_param → self._fsm_state, state names → Constant."""
        for field, child in ast.iter_fields(node):
            if isinstance(child, ast.Name):
                if child.id == state_param:
                    # Replace with self._fsm_state
                    new = ast.Attribute(
                        value=ast.Name(id='self', ctx=ast.Load()),
                        attr='_fsm_state', ctx=ast.Load())
                    setattr(node, field, new)
                elif child.id in state_vals:
                    setattr(node, field, ast.Constant(value=state_vals[child.id]))
            elif isinstance(child, list):
                for j, item in enumerate(child):
                    if isinstance(item, ast.Name):
                        if item.id == state_param:
                            child[j] = ast.Attribute(
                                value=ast.Name(id='self', ctx=ast.Load()),
                                attr='_fsm_state', ctx=ast.Load())
                        elif item.id in state_vals:
                            child[j] = ast.Constant(value=state_vals[item.id])
                    elif isinstance(item, ast.AST):
                        self._fsm_rewrite_expr(item, state_param, state_vals)
            elif isinstance(child, ast.AST):
                self._fsm_rewrite_expr(child, state_param, state_vals)

    def lower_always(self, edges, method):
        """Lower a @posedge/@always block → SeqBlock."""
        self._func = method
        self._reg_locals = {}
        tree = self._get_func_ast(method)
        self._reg_locals = self._scan_reg_locals(tree)
        stmts = self._stmts(tree.body, blocking=False)
        locals_dict = {}
        for name, w in self._reg_locals.items():
            if name not in self._all_reg_locals:
                self._all_reg_locals[name] = w
                locals_dict[name] = w
        self._func = None
        edge_list = [(e.kind, e.signal.name) for e in edges]
        return SeqBlock(edge_list, stmts, locals_dict)

    def lower_formal_prop(self, kind, clock, func):
        """Lower a formal property function → FormalProperty IR node."""
        self._func = func
        self._reg_locals = {}
        tree = self._get_func_ast(func)
        # Extract the return expression from the function body
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and node.value is not None:
                expr = self._expr(node.value)
                self._func = None
                clock_name = clock.name if isinstance(clock, Signal) else str(clock)
                return FormalProperty(kind, clock_name, 'posedge', expr, func.__name__)
        self._func = None
        return None

    def collect_always_targets(self, comb_blocks):
        """Return set of signal names assigned inside always @(*) comb blocks."""
        targets = set()
        for method in comb_blocks:
            tree = self._get_func_ast(method)
            if not all(self._is_nba(s) for s in tree.body):
                self._func = method
                self._reg_locals = {}
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        for t in node.targets:
                            name = self._target_name(t)
                            if name:
                                targets.add(name)
                self._func = None
        return targets

    def collect_block_targets(self, blocks):
        """Return set of signal names assigned in any of the given blocks."""
        targets = set()
        for item in blocks:
            method = item[1] if isinstance(item, tuple) else item
            self._func = method
            tree = self._get_func_ast(method)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        name = self._target_name(t)
                        if name:
                            targets.add(name)
            self._func = None
        return targets


def lower_module(module, module_name=None):
    """Lower a Module to IRModule."""
    from .module import Module

    name = module_name or type(module).__name__.lower()

    # Collect signals, mems, submodules, interfaces
    signals = {}
    mems = {}
    submodules = {}
    interfaces = {}
    for k in dir(module):
        v = getattr(module, k)
        if isinstance(v, (Mem, DualPortMem, TrueDualPortMem)):
            mems[k] = v
        elif isinstance(v, Interface):
            interfaces[k] = v
            for sig_name, sig in v._signals().items():
                signals[f'{k}_{sig_name}'] = sig
        elif isinstance(v, Signal):
            signals[k] = v
        elif isinstance(v, list) and v and all(isinstance(s, Signal) for s in v):
            for i, sig in enumerate(v):
                signals[f'{k}_{i}'] = sig
    for k in dir(module):
        if k.startswith('_'):
            continue
        v = getattr(module, k)
        if isinstance(v, Module) and v is not module:
            submodules[k] = v

    # Collect port arrays (name → list of signal names)
    port_arrays = {}
    for k in dir(module):
        v = getattr(module, k, None)
        if isinstance(v, list) and v and all(isinstance(s, Signal) for s in v):
            port_arrays[k] = [f'{k}_{i}' for i in range(len(v))]

    params = module._params

    # Build IR declarations
    ir = IRModule(name=name)

    # Params
    for pname, pval in params.items():
        ir.params[pname] = pval.default if is_param(pval) else pval

    # Ports
    for sig_name, sig in sorted(signals.items()):
        if sig._kind in ('input', 'output', 'output_reg'):
            w = _width_str(sig)
            direction = 'output' if sig._kind == 'output_reg' else sig._kind
            is_reg = sig._kind == 'output_reg'
            ir.ports.append(Port(sig_name, direction, w, is_reg))

    # Internal regs
    for sig_name, sig in sorted(signals.items()):
        if sig._kind not in ('input', 'output', 'output_reg'):
            w = _width_str(sig)
            ir.regs.append(RegDecl(sig_name, w))

    # Mems
    for mem_name, mem in sorted(mems.items()):
        if isinstance(mem, DualPortMem):
            ir.mems.append(DualPortMemDecl(
                mem_name, mem.depth, mem.width, style=mem.style,
                clock=mem._clock.name, we=mem._we.name,
                waddr=mem._waddr.name, wdata=mem._wdata.name,
                raddr=mem._raddr.name, rdata=mem._rdata.name))
        elif isinstance(mem, TrueDualPortMem):
            ir.mems.append(TrueDualPortMemDecl(
                mem_name, mem.depth, mem.width, style=mem.style,
                clka=mem._clka.name, wea=mem._wea.name,
                addra=mem._addra.name, dina=mem._dina.name,
                douta=mem._douta.name,
                clkb=mem._clkb.name, web=mem._web.name,
                addrb=mem._addrb.name, dinb=mem._dinb.name,
                doutb=mem._doutb.name))
        else:
            w = mem._width_param.name if mem._width_param else mem.width
            d = mem._depth_param.name if mem._depth_param else mem.depth
            ir.mems.append(MemDecl(mem_name, d, w, style=mem.style))

    # Sub-module wires and instances
    lowerer = _Lowerer(signals, mems, submodules, interfaces, params, module, port_arrays=port_arrays)
    always_driven = lowerer.collect_always_targets(module._comb_blocks)

    # Dual-assignment check: error if any signal is assigned in both @comb and @always
    comb_targets = lowerer.collect_block_targets(module._comb_blocks)
    seq_targets_pre = lowerer.collect_block_targets(
        [(e, m) for e, m in module._always_blocks if not getattr(m, '_veripy_cycle', False)]
    )
    dual_driven = comb_targets & seq_targets_pre
    if dual_driven:
        raise ValueError(
            f"Signal(s) assigned in both @comb and @always blocks: {sorted(dual_driven)}"
        )

    for sub_name, sub in sorted(submodules.items()):
        for port_name, sig in sorted(sub._signals().items()):
            if sig._kind in ('input', 'output'):
                w = sig.width  # concrete width for wires
                wire_name = f'{sub_name}_{port_name}'
                if wire_name in always_driven:
                    ir.regs.append(RegDecl(wire_name, w))
                else:
                    ir.wires.append(WireDecl(wire_name, w))

        # Instance
        mod_type = getattr(sub, '_verilog_module_name', None) or _to_snake(type(sub).__name__)
        inst_ports = []
        for port_name, sig in sorted(sub._signals().items()):
            if sig._kind in ('input', 'output'):
                inst_ports.append((port_name, f'{sub_name}_{port_name}'))
        inst_params = {}
        for pk, pv in sub._params.items():
            inst_params[pk] = pv.name if is_param(pv) else pv
        ir.instances.append(Instance(mod_type, sub_name, inst_params, inst_ports))

    # Lower comb blocks
    for method in module._comb_blocks:
        results = lowerer.lower_comb(method, always_driven)
        for item in results:
            if isinstance(item, ContAssign):
                ir.assigns.append(item)
            elif isinstance(item, CombBlock):
                ir.comb_blocks.append(item)

    # Lower always blocks (skip auto-generated mem blocks and cycle-tier blocks)
    for edges, method in module._always_blocks:
        if getattr(method, '_veripy_mem_block', False):
            continue
        if getattr(method, '_veripy_cycle', False):
            continue
        ir.seq_blocks.append(lowerer.lower_always(edges, method))

    # Mark output ports that are driven by always blocks as reg
    seq_targets = set()
    for blk in ir.seq_blocks:
        for s in blk.stmts:
            _collect_assign_targets(s, seq_targets)
    for blk in ir.comb_blocks:
        for s in blk.stmts:
            _collect_assign_targets(s, always_driven)
    # Also mark outputs driven by dual-port memory read ports
    for m in ir.mems:
        if isinstance(m, DualPortMemDecl):
            seq_targets.add(m.rdata)
        elif isinstance(m, TrueDualPortMemDecl):
            seq_targets.add(m.douta)
            seq_targets.add(m.doutb)
    for port in ir.ports:
        if port.direction == 'output' and (port.name in seq_targets or port.name in always_driven):
            port.is_reg = True

    # Internal signals driven by continuous assign must be wire, not reg
    cont_targets = {a.target for a in ir.assigns}
    new_regs = []
    for r in ir.regs:
        if r.name in cont_targets:
            ir.wires.append(WireDecl(r.name, r.width))
        else:
            new_regs.append(r)
    ir.regs = new_regs

    # Lower formal properties (assert_always, cover, assume)
    for clock, func in module._assertions:
        prop = lowerer.lower_formal_prop('assert', clock, func)
        if prop:
            ir.formal_props.append(prop)
    for clock, func, _hit in module._covers:
        prop = lowerer.lower_formal_prop('cover', clock, func)
        if prop:
            ir.formal_props.append(prop)
    for clock, func in getattr(module, '_assumes', []):
        prop = lowerer.lower_formal_prop('assume', clock, func)
        if prop:
            ir.formal_props.append(prop)

    return ir


def _collect_assign_targets(stmt, targets):
    if isinstance(stmt, Assign):
        targets.add(stmt.target)
    elif isinstance(stmt, SliceAssign):
        targets.add(stmt.target)
    elif isinstance(stmt, MemWrite):
        targets.add(stmt.mem)
    elif isinstance(stmt, If):
        for s in stmt.then_body:
            _collect_assign_targets(s, targets)
        for s in stmt.else_body:
            _collect_assign_targets(s, targets)
    elif isinstance(stmt, Case):
        for _, body in stmt.cases:
            for s in body:
                _collect_assign_targets(s, targets)
        if stmt.default:
            for s in stmt.default:
                _collect_assign_targets(s, targets)


# ── Testbench lowering ───────────────────────────────────────────────

class _TBLowerer:
    """Lowers testbench @always/@initial blocks to IR.

    Unlike _Lowerer (which resolves self.signal), this handles:
    - m.signal / m.iface.signal references (module variable)
    - self.set(name=val) → blocking assign
    - m.signal.set(val) → blocking assign
    - yield expr → Delay
    - self.out('name') → Display
    - int(expr) → strip wrapper
    - self.assertEqual/assertTrue → skip
    """

    def __init__(self, mod, mod_var_name, output_names):
        self.mod = mod
        self.mod_var = mod_var_name  # e.g. 'm'
        self.output_names = output_names
        self._func = None
        self._tc_var = 'self'  # default, updated by _detect_tc_var
        # Build signal name map: flat name for each signal
        self._sig_names = {}
        for k in dir(mod):
            v = getattr(mod, k)
            if isinstance(v, Signal):
                self._sig_names[k] = k
            elif isinstance(v, Interface):
                for sn in v._signals():
                    self._sig_names[f'{k}.{sn}'] = f'{k}_{sn}'

    def lower(self, func):
        """Lower a testbench function → list[Stmt]."""
        self._func = func
        self._local_dicts = {}  # name → dict value (for d = _defaults(); d.update(); self.set(**d))
        # Detect test case variable name from closure
        if hasattr(func, '__code__') and func.__closure__:
            for i, name in enumerate(func.__code__.co_freevars):
                val = func.__closure__[i].cell_contents
                if hasattr(val, '_mod') and hasattr(val, 'out'):  # VeripyTestCase
                    self._tc_var = name
                    break
        # Also check globals (for top-level scripts where tc is a global)
        if self._tc_var == 'self' and hasattr(func, '__globals__'):
            for name, val in func.__globals__.items():
                if hasattr(val, '_mod') and hasattr(val, 'out') and val._mod is self.mod:
                    self._tc_var = name
                    break
        src = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(src)
        func_def = tree.body[0]
        return self._stmts(func_def.body)

    def _stmts(self, nodes):
        out = []
        for node in nodes:
            out.extend(self._stmt(node))
        return out

    def _stmt(self, node):
        # yield expr → Delay
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Yield):
            return [Delay(self._expr(node.value.value))]

        # yield from self._method() → inline the method body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.YieldFrom):
            return self._inline_yield_from(node.value.value)

        # self.set(name=val, ...) → blocking assigns
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if self._is_tc_method(call, 'set'):
                stmts = []
                for kw in call.keywords:
                    if kw.arg is None:
                        # **kwargs — try to evaluate and expand
                        val = self._try_eval(kw.value)
                        if val is None and isinstance(kw.value, ast.Name):
                            val = self._local_dicts.get(kw.value.id)
                        if isinstance(val, dict):
                            for k, v in val.items():
                                stmts.append(Assign(k, Const(v), blocking=True))
                        continue
                    stmts.append(Assign(kw.arg, self._expr(kw.value), blocking=True))
                return stmts

            # m.signal.set(val) or m.iface.signal.set(val)
            sig_name = self._is_sig_set(call)
            if sig_name:
                return [Assign(sig_name, self._expr(call.args[0]), blocking=True)]

            # d.update(key=val, ...) — mutate tracked local dict
            if (isinstance(call.func, ast.Attribute) and call.func.attr == 'update'
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in self._local_dicts):
                d = self._local_dicts[call.func.value.id]
                for kw in call.keywords:
                    if kw.arg is not None:
                        try:
                            d[kw.arg] = self._const_eval(kw.value)
                        except SyntaxError:
                            pass
                return []

            # self.assertEqual / self.assertTrue / self.assertXxx → extract self.out calls
            if self._is_tc_method_any(call, ('assertEqual', 'assertTrue',
                                              'assertFalse', 'assertIn')):
                return self._extract_out_displays(call)

            # self.out('name') as statement → Display
            if self._is_tc_method(call, 'out') and call.args:
                return [self._make_display(call.args[0].value)]

        # if/elif/else
        if isinstance(node, ast.If):
            return [If(
                self._expr(node.test),
                self._stmts(node.body),
                self._stmts(node.orelse) if node.orelse else [],
            )]

        # for _ in range(N)
        if isinstance(node, ast.For):
            return self._lower_for(node)

        # Local variable assignment: x = expr
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                # Try to evaluate as dict for d = _defaults() pattern
                val = self._try_eval(node.value)
                if isinstance(val, dict):
                    self._local_dicts[target.id] = val
                    return []
                return [Assign(target.id, self._expr(node.value), blocking=True)]
            # m.signal = val (shouldn't happen in testbenches, but handle it)
            sig_name = self._resolve_sig_ref(target)
            if sig_name:
                return [Assign(sig_name, self._expr(node.value), blocking=True)]

        # break → disable named block
        if isinstance(node, ast.Break):
            if hasattr(self, '_break_label') and self._break_label:
                return [Disable(self._break_label)]
            return []

        # Fallback: scan for self.out() calls anywhere in the statement
        return self._scan_out_calls(node)

    def _scan_out_calls(self, node):
        """Walk an AST node and emit Display for any self.out('name') calls."""
        stmts = []
        for child in ast.walk(node):
            if not (isinstance(child, ast.Call)
                    and self._is_tc_method(child, 'out') and child.args):
                continue
            arg = child.args[0]
            if isinstance(arg, ast.Constant):
                stmts.append(self._make_display(arg.value))
        # If no constant-arg out() found, check for comprehensions over output_names
        if not stmts:
            for child in ast.walk(node):
                if isinstance(child, (ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                    for gen in child.generators:
                        if self._is_tc_attr(gen.iter, '_output_names'):
                            for name in self.output_names:
                                stmts.append(self._make_display(name))
                            return stmts
        return stmts

    def _inline_yield_from(self, call_node):
        """Inline a yield from self._method(...) call by parsing the method source."""
        if not (isinstance(call_node, ast.Call) and isinstance(call_node.func, ast.Attribute)):
            return []
        obj = call_node.func.value
        method_name = call_node.func.attr
        if not (isinstance(obj, ast.Name) and obj.id == self._tc_var):
            return []
        # Find the method
        method = None
        if hasattr(self._func, '__globals__'):
            for cls in self._func.__globals__.values():
                if isinstance(cls, type) and hasattr(cls, method_name):
                    method = getattr(cls, method_name)
                    break
        if method is None:
            return []
        src = textwrap.dedent(inspect.getsource(method))
        tree = ast.parse(src)
        func_def = tree.body[0]
        # Build param→arg substitution (skip 'self')
        params = [p.arg for p in func_def.args.args[1:]]  # skip self
        if params and call_node.args:
            mapping = dict(zip(params, call_node.args))
            func_def = _SubstArgs(mapping).visit(func_def)
        return self._stmts(func_def.body)

    def _is_tc_attr(self, node, attr):
        """Check if node is self.<attr>."""
        return (isinstance(node, ast.Attribute)
                and node.attr == attr
                and isinstance(node.value, ast.Name)
                and node.value.id == self._tc_var)

    def _expr(self, node):
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return Const(1 if v else 0)
            return Const(v)

        if isinstance(node, ast.Name):
            name = node.id
            # Try to resolve via closure/globals
            val = self._resolve_name(name)
            if val is not None:
                if isinstance(val, (int, float)):
                    return Const(val)
                # Could be a signal name
            if name in self._sig_names:
                return Sig(name)
            return Sig(name)  # local variable

        # int(expr) → strip wrapper
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'int' and len(node.args) == 1):
            return self._expr(node.args[0])

        # Function call with all-constant args → evaluate
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = self._resolve_name(node.func.id)
            if callable(fn):
                try:
                    args = [self._const_eval(a) for a in node.args]
                    kwargs = {kw.arg: self._const_eval(kw.value) for kw in node.keywords}
                    return Const(fn(*args, **kwargs))
                except (SyntaxError, TypeError):
                    pass

        # self.out('name') in expression context → signal ref
        if isinstance(node, ast.Call) and self._is_tc_method(node, 'out') and node.args:
            name = node.args[0].value
            return Sig(name)

        # m.signal or m.iface.signal
        sig_name = self._resolve_sig_ref(node)
        if sig_name:
            return Sig(sig_name)

        # not expr
        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op:
                return UnaryOp(op, self._expr(node.operand))

        # binary ops
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op:
                return BinOp(op, self._expr(node.left), self._expr(node.right))

        # comparisons
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = _CMP_OPS.get(type(node.ops[0]))
            if op:
                return Compare(op, self._expr(node.left), self._expr(node.comparators[0]))

        # bool ops
        if isinstance(node, ast.BoolOp):
            op = _BOOL_OPS.get(type(node.op))
            if op:
                return BoolOp(op, [self._expr(v) for v in node.values])

        # ternary: a if cond else b
        if isinstance(node, ast.IfExp):
            return Mux(self._expr(node.test), self._expr(node.body), self._expr(node.orelse))

        raise SyntaxError(f'TB: unsupported expression: {ast.dump(node)}')

    def _resolve_name(self, name):
        """Resolve a bare name via closure/globals. Returns value or None."""
        func = self._func
        if func and hasattr(func, '__code__'):
            code = func.__code__
            if name in code.co_freevars and func.__closure__:
                idx = code.co_freevars.index(name)
                return func.__closure__[idx].cell_contents
            if hasattr(func, '__globals__') and name in func.__globals__:
                return func.__globals__[name]
        return None

    def _try_eval(self, node):
        """Try to evaluate an AST node to a Python value."""
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = self._resolve_name(node.func.id)
            if callable(fn):
                try:
                    args = [self._try_eval(a) for a in node.args]
                    kwargs = {kw.arg: self._try_eval(kw.value) for kw in node.keywords}
                    return fn(*args, **kwargs)
                except Exception:
                    pass
        if isinstance(node, ast.Constant):
            return node.value
        return None

    def _resolve_sig_ref(self, node):
        """Resolve m.signal or m.iface.signal to flat signal name."""        # m.iface.signal
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == self.mod_var):
            iface = node.value.attr
            key = f'{iface}.{node.attr}'
            return self._sig_names.get(key)
        # m.signal
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == self.mod_var):
            return self._sig_names.get(node.attr)
        return None

    def _is_sig_set(self, call):
        """Check if call is m.signal.set(val) or m.iface.signal.set(val)."""
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == 'set'
                and len(call.args) == 1):
            return None
        return self._resolve_sig_ref(call.func.value)

    def _is_tc_method(self, call, name):
        """Check if call is <tc_var>.name(...)."""
        return (isinstance(call.func, ast.Attribute)
                and call.func.attr == name
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == self._tc_var)

    def _is_tc_method_any(self, call, names):
        return (isinstance(call.func, ast.Attribute)
                and call.func.attr in names
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == self._tc_var)

    def _make_display(self, name):
        return Display(f"@%0t {name}=%0d", [Sig('$time'), Sig(name)])

    def _extract_out_displays(self, call):
        """Extract self.out('name') calls from assertion args → Display stmts."""
        stmts = []
        for arg in call.args:
            for child in ast.walk(arg):
                if (isinstance(child, ast.Call) and self._is_tc_method(child, 'out')
                        and child.args and isinstance(child.args[0], ast.Constant)):
                    stmts.append(self._make_display(child.args[0].value))
        return stmts

    def _lower_for(self, node):
        """Emit Repeat or ForLoop instead of unrolling."""
        if not (isinstance(node.iter, ast.Call)
                and isinstance(node.iter.func, ast.Name)
                and node.iter.func.id == 'range'):
            raise SyntaxError('TB: only for ... in range(...) supported')
        args = node.iter.args
        n = len(args)
        start = self._const_eval(args[0]) if n >= 2 else 0
        stop = self._const_eval(args[0]) if n == 1 else self._const_eval(args[1])

        has_break = any(isinstance(n_, ast.Break)
                        for n_ in ast.walk(ast.Module(body=node.body, type_ignores=[])))
        label = ''
        if has_break:
            if not hasattr(self, '_loop_id'):
                self._loop_id = 0
            label = f'_loop{self._loop_id}'
            self._loop_id += 1
            self._break_label = label

        var = node.target.id
        body = self._stmts(node.body)

        if has_break:
            self._break_label = None

        if var == '_':
            return [Repeat(Const(stop - start), body, label)]
        else:
            return [ForLoop(var, Const(start), Const(stop), body, label)]

    def _const_eval(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            val = self._resolve_name(node.id)
            if isinstance(val, (int, float)):
                return val
        if isinstance(node, ast.BinOp):
            l = self._const_eval(node.left)
            r = self._const_eval(node.right)
            ops = {ast.Add: lambda a,b: a+b, ast.Sub: lambda a,b: a-b,
                   ast.Mult: lambda a,b: a*b}
            fn = ops.get(type(node.op))
            if fn:
                return fn(l, r)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._const_eval(node.operand)
        raise SyntaxError(f'TB: not constant: {ast.dump(node)}')


class _SubstArgs(ast.NodeTransformer):
    """Replace Name nodes matching parameter names with argument expressions."""
    def __init__(self, mapping):
        self.mapping = mapping
    def visit_Name(self, node):
        if node.id in self.mapping:
            return ast.copy_location(self.mapping[node.id], node)
        return node


def lower_tb_block(func, mod, mod_var_name, output_names):
    """Lower a testbench function → list[Stmt].

    func: the @always or @initial generator function
    mod: the Module instance (for signal discovery)
    mod_var_name: variable name used to reference the module (e.g. 'm')
    output_names: list of output signal names to record
    """
    lowerer = _TBLowerer(mod, mod_var_name, output_names)
    return lowerer.lower(func)
