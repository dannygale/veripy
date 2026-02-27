"""AST rewriter: transforms bare signal assignments in @comb/@posedge bodies.

Given a set of signal names, rewrites:
    x = expr        →  x._assign(expr)
    x.y = expr      →  x.y._assign(expr)   (sub-module port drives)
    x[i] = expr     →  x[i]._assign(expr)  (bit/slice writes)
"""

import ast
import inspect
import textwrap


class _SignalAssignRewriter(ast.NodeTransformer):
    """Rewrite assignments to known signal names into _assign() calls."""

    def __init__(self, signal_names, submodule_names):
        self.signal_names = signal_names
        self.submodule_names = submodule_names

    def visit_Assign(self, node):
        # Single target only: x = expr
        if len(node.targets) != 1:
            return self.generic_visit(node)
        target = node.targets[0]
        call = self._make_assign_call(target, node.value)
        if call:
            return ast.copy_location(ast.Expr(value=call), node)
        return self.generic_visit(node)

    def visit_AugAssign(self, node):
        # x += expr → x._assign(x <op> expr)
        if isinstance(node.target, ast.Name) and node.target.id in self.signal_names:
            binop = ast.BinOp(
                left=ast.Name(id=node.target.id, ctx=ast.Load()),
                op=node.op, right=node.value)
            call = ast.Call(
                func=ast.Attribute(value=ast.Name(id=node.target.id, ctx=ast.Load()),
                                   attr='_assign', ctx=ast.Load()),
                args=[binop], keywords=[])
            return ast.copy_location(ast.Expr(value=call), node)
        return self.generic_visit(node)

    def _make_assign_call(self, target, value):
        # x = expr where x is a signal
        if isinstance(target, ast.Name) and target.id in self.signal_names:
            return ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id=target.id, ctx=ast.Load()),
                    attr='_assign', ctx=ast.Load()),
                args=[value], keywords=[])
        # x.y = expr where x is a submodule
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            if target.value.id in self.submodule_names:
                attr_access = ast.Attribute(
                    value=ast.Name(id=target.value.id, ctx=ast.Load()),
                    attr=target.attr, ctx=ast.Load())
                return ast.Call(
                    func=ast.Attribute(value=attr_access, attr='_assign', ctx=ast.Load()),
                    args=[value], keywords=[])
        # x[i] = expr where x is a signal
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            if target.value.id in self.signal_names:
                # Deep-copy slice and fix any Store contexts to Load
                import copy
                fixed_slice = copy.deepcopy(target.slice)
                for node in ast.walk(fixed_slice) if not isinstance(fixed_slice, (int, str)) else []:
                    if hasattr(node, 'ctx') and isinstance(node.ctx, ast.Store):
                        node.ctx = ast.Load()
                subscript = ast.Subscript(
                    value=ast.Name(id=target.value.id, ctx=ast.Load()),
                    slice=fixed_slice, ctx=ast.Load())
                return ast.Call(
                    func=ast.Attribute(value=subscript, attr='_assign', ctx=ast.Load()),
                    args=[value], keywords=[])
        return None


def rewrite_assigns(func, signal_names, submodule_names=frozenset(), extra_globals=None):
    """Rewrite a function's signal assignments and return a new callable.

    Returns a compiled function with bare `x = expr` rewritten to `x._assign(expr)`
    for any x in signal_names.
    """
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)

    # Find the function def (skip decorators)
    func_def = tree.body[0]
    assert isinstance(func_def, (ast.FunctionDef, ast.AsyncFunctionDef))

    rewriter = _SignalAssignRewriter(signal_names, submodule_names)
    func_def = rewriter.visit(func_def)
    ast.fix_missing_locations(tree)

    # Strip decorators to avoid re-applying them
    func_def.decorator_list = []

    code = compile(tree, inspect.getfile(func), 'exec')
    namespace = {}
    namespace.update(func.__globals__)
    if extra_globals:
        namespace.update(extra_globals)
    # Inject closure variables
    if func.__code__.co_freevars and func.__closure__:
        for i, name in enumerate(func.__code__.co_freevars):
            namespace[name] = func.__closure__[i].cell_contents
    exec(code, namespace)
    return namespace[func.__name__]
