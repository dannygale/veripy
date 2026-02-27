"""@module decorator: declarative module definitions without class/self boilerplate."""

import ast
import copy
import inspect
import textwrap

from .signal import Signal, Mem
from .parameter import Parameter, ParamExpr, is_param
from .module import Module, DeferredModule, _resolve_deferred
from .context import ModuleContext, _set_context, _get_context
from .rewriter import _SignalAssignRewriter


class _SelfPrefixer(ast.NodeTransformer):
    """Add self. prefix to bare signal/submodule names for emitter compatibility."""

    def __init__(self, names):
        self.names = names

    def visit_Name(self, node):
        if node.id in self.names:
            return ast.Attribute(
                value=ast.Name(id='self', ctx=ast.Load()),
                attr=node.id, ctx=node.ctx)
        return node


def _analyze_and_rewrite(func):
    """Parse function AST, extract signal/submodule names, rewrite assignments,
    append return locals(), and compile. Also build self-prefixed inner functions
    for the emitter."""
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    func_def = tree.body[0]
    func_def.decorator_list = []

    # Extract signal and submodule names from top-level assignments
    signal_names = set()
    submodule_names = set()
    for node in func_def.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
            continue
        fname = None
        if isinstance(node.value.func, ast.Name):
            fname = node.value.func.id
        if fname in ('Input', 'Output', 'Register', 'Signal', 'Mem'):
            signal_names.add(target.id)
        elif fname and fname not in ('Parameter',):
            submodule_names.add(target.id)

    # Build self-prefixed source for each inner logic block (for emitter)
    all_names = signal_names | submodule_names
    emitter_sources = {}  # func_name → source string
    for node in func_def.body:
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            prefixed = copy.deepcopy(node)
            prefixed.decorator_list = []
            prefixed.args.args.insert(0, ast.arg(arg='self'))
            _SelfPrefixer(all_names).visit(prefixed)
            ast.fix_missing_locations(prefixed)
            wrapper = ast.Module(body=[prefixed], type_ignores=[])
            emitter_sources[node.name] = ast.unparse(wrapper)

    # Rewrite assignments inside inner functions for simulation
    rewriter = _SignalAssignRewriter(signal_names, submodule_names)
    for node in func_def.body:
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            node.body = [rewriter.visit(stmt) for stmt in node.body]

    # Append return locals()
    func_def.body.append(ast.Return(value=ast.Call(
        func=ast.Name(id='locals', ctx=ast.Load()), args=[], keywords=[])))
    ast.fix_missing_locations(tree)
    code = compile(tree, inspect.getfile(func), 'exec')
    ns = dict(func.__globals__)
    exec(code, ns)
    rewritten_func = ns[func.__name__]

    return rewritten_func, signal_names, submodule_names, emitter_sources


def module(func):
    """Decorator that turns a function into a Module factory.

    Usage:
        @module
        def counter(width=8):
            clock  = Input()
            count  = Output(width)
            cnt    = Register(width)

            @comb
            def drive():
                count = cnt

            @posedge(clock)
            def increment():
                if reset:
                    cnt = 0
                elif enable:
                    cnt = cnt + 1

        c = counter(width=4)
    """
    sig = inspect.signature(func)
    param_names = {
        name: p.default for name, p in sig.parameters.items()
        if p.default is not inspect.Parameter.empty
    }

    rewritten_func, signal_names, submodule_names, emitter_sources = _analyze_and_rewrite(func)

    # Create a named Module subclass
    mod_cls = type(func.__name__, (Module,), {})

    def factory(**kwargs):
        resolved_params = {k: kwargs.get(k, v) for k, v in param_names.items()}

        ctx = ModuleContext()
        prev_ctx = _get_context()
        _set_context(ctx)
        try:
            local_vars = rewritten_func(**resolved_params)
        finally:
            _set_context(prev_ctx)

        # Build module instance
        instance = object.__new__(mod_cls)
        instance._always_blocks = []
        instance._comb_blocks = []
        instance._assertions = []
        instance._covers = []
        instance._timing = []
        instance._params = resolved_params

        # Attach signals, mems, submodules
        for name, val in local_vars.items():
            if isinstance(val, Signal):
                val.name = name
                object.__setattr__(instance, name, val)
            elif isinstance(val, Mem):
                val.name = name
                object.__setattr__(instance, name, val)
            elif isinstance(val, DeferredModule):
                resolved = _resolve_deferred(val, resolved_params)
                object.__setattr__(instance, name, resolved)
            elif isinstance(val, Module):
                object.__setattr__(instance, name, val)

        # Collect logic blocks with emitter source attached
        for fn in ctx.comb_blocks:
            src = emitter_sources.get(fn.__name__)
            if src:
                fn._veripy_emit_source = src
            instance._comb_blocks.append(fn)

        for edges, fn in ctx.always_blocks:
            src = emitter_sources.get(fn.__name__)
            if src:
                fn._veripy_emit_source = src
            instance._always_blocks.append((edges, fn))

        return instance

    factory.__name__ = func.__name__
    factory.__qualname__ = func.__qualname__
    return factory
