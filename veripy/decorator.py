"""@module decorator: declarative module definitions without class/self boilerplate."""

import ast
import copy
import inspect
import textwrap

from .signal import Signal, Mem, DualPortMem, TrueDualPortMem, Interface
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
        if not isinstance(target, ast.Name):
            continue
        # List comprehension of signals: data_in = [Input(w) for _ in range(n)]
        if (isinstance(node.value, ast.ListComp) and
                isinstance(node.value.elt, ast.Call) and
                isinstance(node.value.elt.func, ast.Name) and
                node.value.elt.func.id in ('Input', 'Output', 'Register', 'Signal')):
            signal_names.add(target.id)
            continue
        if not isinstance(node.value, ast.Call):
            continue
        fname = None
        if isinstance(node.value.func, ast.Name):
            fname = node.value.func.id
        if fname in ('Input', 'Output', 'Register', 'Signal', 'Mem',
                     'DualPortMem', 'TrueDualPortMem'):
            signal_names.add(target.id)
        elif fname and fname not in ('Parameter', 'pipeline',
                                      'create_clock', 'max_delay', 'false_path'):
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

            @always(posedge(clock))
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

        # Create Parameter objects so signals get _width_param
        param_objs = {}
        call_params = {}
        for k, v in resolved_params.items():
            if is_param(v):
                call_params[k] = v
            else:
                p = Parameter(v)
                p.name = k
                param_objs[k] = p
                call_params[k] = p

        ctx = ModuleContext()
        prev_ctx = _get_context()
        _set_context(ctx)
        try:
            local_vars = rewritten_func(**call_params)
        finally:
            _set_context(prev_ctx)

        # Build module instance
        instance = object.__new__(mod_cls)
        instance._always_blocks = []
        instance._comb_blocks = []
        instance._assertions = []
        instance._covers = []
        instance._assumes = []
        instance._timing = []
        instance._params = resolved_params
        instance._functional = None
        instance._cycle = None
        instance._iss = None
        instance._behavioral = None  # migration alias

        # Attach signals, mems, submodules
        for name, val in local_vars.items():
            if isinstance(val, Signal):
                val.name = name
                object.__setattr__(instance, name, val)
            elif isinstance(val, Mem):
                val.name = name
                object.__setattr__(instance, name, val)
            elif isinstance(val, (DualPortMem, TrueDualPortMem)):
                val.name = name
                object.__setattr__(instance, name, val)
                val._register(instance)
            elif isinstance(val, Interface):
                object.__setattr__(instance, name, val)
                # Flatten interface signals with prefix
                for sig_name, sig in val._signals().items():
                    sig.name = f'{name}_{sig_name}'
            elif isinstance(val, list) and val and all(isinstance(s, Signal) for s in val):
                for i, sig in enumerate(val):
                    sig.name = f'{name}_{i}'
                object.__setattr__(instance, name, val)
            elif isinstance(val, DeferredModule):
                resolved = _resolve_deferred(val, resolved_params)
                object.__setattr__(instance, name, resolved)
            elif isinstance(val, Module):
                object.__setattr__(instance, name, val)

        # Attach any signals registered on context (e.g. FSM state, pipeline stages)
        for name, sig in ctx.signals.items():
            if not hasattr(instance, name):
                object.__setattr__(instance, name, sig)

        # Finalize any pipelines before collecting blocks
        from .module import _Pipeline
        for name, val in local_vars.items():
            if isinstance(val, _Pipeline):
                val._module = instance
                val._finalize()

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

        # Collect formal properties, timing, and FSM signals from context
        instance._assertions = list(ctx.assertions)
        instance._covers = list(ctx.covers)
        instance._assumes = list(ctx.assumes)
        if ctx.functional_fn is not None:
            instance._functional = ctx.functional_fn
        if ctx.cycle_fn is not None:
            instance._cycle = ctx.cycle_fn
        if ctx.iss_fn is not None:
            instance._iss = ctx.iss_fn
        # migration alias
        instance._behavioral = instance._functional
        for clock, fn in instance._assertions:
            src = emitter_sources.get(fn.__name__)
            if src:
                fn._veripy_emit_source = src
        for clock, fn, _hit in instance._covers:
            src = emitter_sources.get(fn.__name__)
            if src:
                fn._veripy_emit_source = src
        for clock, fn in instance._assumes:
            src = emitter_sources.get(fn.__name__)
            if src:
                fn._veripy_emit_source = src
        # Resolve timing constraint signal refs to names
        resolved_timing = []
        for entry in ctx.timing:
            if entry[0] == 'create_clock':
                _, sig, period = entry
                resolved_timing.append(('create_clock', sig.name if isinstance(sig, Signal) else sig, period))
            elif entry[0] == 'max_delay':
                _, fr, to, ns = entry
                resolved_timing.append(('max_delay',
                                        fr.name if isinstance(fr, Signal) else fr,
                                        to.name if isinstance(to, Signal) else to, ns))
            elif entry[0] == 'false_path':
                _, fr, to = entry
                resolved_timing.append(('false_path',
                                        fr.name if isinstance(fr, Signal) else fr,
                                        to.name if isinstance(to, Signal) else to))
        instance._timing = resolved_timing
        instance._clock_domains = {
            name: (clk.name if isinstance(clk, Signal) else clk)
            for name, clk in ctx.clock_domains.items()
        }

        # Attach any FSM signals registered on context
        for name, sig in ctx.signals.items():
            if not hasattr(instance, name):
                object.__setattr__(instance, name, sig)

        return instance

    factory.__name__ = func.__name__
    factory.__qualname__ = func.__qualname__
    factory._is_veripy_module = True
    factory._param_names = param_names
    mod_cls._veripy_factory = factory
    return factory
