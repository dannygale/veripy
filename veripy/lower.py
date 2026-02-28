"""Lowering pass: Module → IRModule.

Resolves all Python names, folds constants, classifies locals,
detects case chains, and produces a clean IR with no Python AST references.
"""

import ast
import inspect
import textwrap

from .ir import (
    Expr, Const, Param, Sig, BinOp, UnaryOp, Compare, BoolOp, Mux,
    Slice, Index, Concat,
    Stmt, Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock,
    Port, WireDecl, RegDecl, MemDecl, Instance, IRModule,
)
from .signal import Signal, Mem, Interface
from .parameter import is_param

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

    def __init__(self, signals, mems, submodules, interfaces, params):
        self.signals = signals
        self.mems = mems
        self.submodules = submodules
        self.interfaces = interfaces
        self.params = params
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
                # Plain local — treat as reg
                if name not in self._reg_locals:
                    self._reg_locals[name] = 32
                return [Assign(name, self._expr(stmt.value), blocking=True)]

            # self.x[slice] = val
            if isinstance(target, ast.Subscript) and self._is_self_target(target):
                tname = self._target_name(target.value)
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

        return []  # skip unrecognized (docstrings, etc.)

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

    def _lower_for(self, node, blocking):
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
            start, stop, step = args
        var = node.target.id
        out = []
        for val in range(start, stop, step):
            body = self._subst_var(node.body, var, val)
            out.extend(self._stmts(body, blocking))
        return out

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
                        regs[name] = 32
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
        if isinstance(v, Mem):
            mems[k] = v
        elif isinstance(v, Interface):
            interfaces[k] = v
            for sig_name, sig in v._signals().items():
                signals[f'{k}_{sig_name}'] = sig
        elif isinstance(v, Signal):
            signals[k] = v
    for k in dir(module):
        if k.startswith('_'):
            continue
        v = getattr(module, k)
        if isinstance(v, Module) and v is not module:
            submodules[k] = v

    params = module._params

    # Build IR declarations
    ir = IRModule(name=name)

    # Params
    for pname, pval in params.items():
        ir.params[pname] = pval.default if is_param(pval) else pval

    # Ports
    for sig_name, sig in sorted(signals.items()):
        if sig._kind in ('input', 'output'):
            w = _width_str(sig)
            is_reg = False
            ir.ports.append(Port(sig_name, sig._kind, w, is_reg))

    # Internal regs
    for sig_name, sig in sorted(signals.items()):
        if sig._kind not in ('input', 'output'):
            w = _width_str(sig)
            ir.regs.append(RegDecl(sig_name, w))

    # Mems
    for mem_name, mem in sorted(mems.items()):
        w = mem._width_param.name if mem._width_param else mem.width
        d = mem._depth_param.name if mem._depth_param else mem.depth
        ir.mems.append(MemDecl(mem_name, d, w))

    # Sub-module wires and instances
    lowerer = _Lowerer(signals, mems, submodules, interfaces, params)
    always_driven = lowerer.collect_always_targets(module._comb_blocks)

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
        mod_type = _to_snake(type(sub).__name__)
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

    # Lower always blocks
    for edges, method in module._always_blocks:
        ir.seq_blocks.append(lowerer.lower_always(edges, method))

    # Mark output ports that are driven by always blocks as reg
    seq_targets = set()
    for blk in ir.seq_blocks:
        for s in blk.stmts:
            _collect_assign_targets(s, seq_targets)
    for blk in ir.comb_blocks:
        for s in blk.stmts:
            _collect_assign_targets(s, always_driven)
    for port in ir.ports:
        if port.direction == 'output' and (port.name in seq_targets or port.name in always_driven):
            port.is_reg = True

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
