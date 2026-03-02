"""Module base class: defines the structure for simulation and Verilog emission."""

import ast as _ast
from .signal import Signal, Mem, DualPortMem, TrueDualPortMem, Edge, SensitivityList, Interface, Register, posedge as _posedge, _Expr, _SliceProxy
from .parameter import Parameter, ParamExpr, is_param


class DeferredModule:
    """Placeholder for a sub-module whose parameters aren't resolved yet."""
    __slots__ = ('cls', 'args', 'kwargs')

    def __init__(self, cls, args, kwargs):
        self.cls = cls
        self.args = args
        self.kwargs = kwargs


def _resolve_deferred(deferred, param_values):
    """Instantiate a DeferredModule with resolved parameter values."""
    def _resolve_val(v):
        if isinstance(v, Parameter):
            return param_values[v.name]
        if isinstance(v, ParamExpr):
            return v.resolve(param_values)
        return v
    args = tuple(_resolve_val(a) for a in deferred.args)
    kwargs = {k: _resolve_val(v) for k, v in deferred.kwargs.items()}
    return deferred.cls(*args, **kwargs)


class Module:
    """Base class for hardware modules.

    Subclasses define signals in __init__, then register combinational
    and sequential blocks via @self.comb, @self.posedge, @self.negedge,
    or @self.always decorators.
    Sub-modules are any Module-typed attributes (self.alu = ALU(...)).
    """

    def __new__(cls, *args, **kwargs):
        has_param = any(is_param(v) for v in args) or \
                    any(is_param(v) for v in kwargs.values())
        if has_param:
            return DeferredModule(cls, args, kwargs)
        return super().__new__(cls)

    def __setattr__(self, name, value):
        # Always check if we're assigning to an existing Signal (even underscore-prefixed)
        try:
            existing = object.__getattribute__(self, name)
            if isinstance(existing, Signal):
                existing._assign(value)
                return
            if not name.startswith('_') and isinstance(existing, Interface) and isinstance(value, Interface):
                for name, direction in Interface._match(existing, value):
                    l_sig = existing._signals()[name]
                    r_sig = value._signals()[name]
                    if direction == 'r2l':
                        l_sig._assign(int(r_sig))
                    elif direction == 'l2r':
                        r_sig._assign(int(l_sig))
                    else:  # fwd
                        if l_sig._kind == 'input':
                            l_sig._assign(int(r_sig))
                        else:
                            r_sig._assign(int(l_sig))
                return
        except AttributeError:
            pass
        object.__setattr__(self, name, value)

    def __init__(self, params=None):
        self._always_blocks = []   # [(edges_list, method), ...]
        self._comb_blocks = []
        self._assertions = []      # [(clock_signal, func), ...]
        self._covers = []          # [(clock_signal, func, hit), ...]
        self._assumes = []         # [(clock_signal, func), ...]
        self._timing = []          # [(constraint_type, kwargs), ...]
        self._behavioral = None    # optional behavioral model function
        if params is not None:
            self._params = params
            for name, val in params.items():
                if isinstance(val, Parameter) and val.name is None:
                    val.name = name
        else:
            # Auto-capture constructor kwargs as Verilog parameters
            import inspect
            self._params = {}
            init = type(self).__init__
            if init is not Module.__init__:
                frame = inspect.currentframe().f_back
                sig = inspect.signature(init)
                for name, param in sig.parameters.items():
                    if name == 'self':
                        continue
                    if name in frame.f_locals:
                        self._params[name] = frame.f_locals[name]
        for attr in dir(self):
            val = getattr(self, attr)
            if isinstance(val, Signal) and not val.name:
                val.name = attr
            elif isinstance(val, Mem) and not val.name:
                val.name = attr
            elif isinstance(val, (DualPortMem, TrueDualPortMem)) and not val.name:
                val.name = attr
                val._register(self)
            elif isinstance(val, Interface):
                for sig_name, sig in val._signals().items():
                    sig.name = f'{attr}_{sig_name}'

    def always(self, sensitivity):
        """Decorator: register a method with an explicit sensitivity list.

        Usage:
            @self.always(posedge(self.clk) | negedge(self.rst))
            def logic(): ...
        """
        if isinstance(sensitivity, Edge):
            edges = [sensitivity]
        elif isinstance(sensitivity, SensitivityList):
            edges = sensitivity.edges
        else:
            raise TypeError(f"Expected Edge or SensitivityList, got {type(sensitivity)}")
        def decorator(method):
            self._always_blocks.append((edges, method))
            return method
        return decorator

    def posedge(self, clock_signal):
        """Decorator: sugar for @self.always(posedge(clk))."""
        return self.always(_posedge(clock_signal))

    def negedge(self, clock_signal):
        """Decorator: sugar for @self.always(negedge(clk))."""
        from .signal import negedge as _negedge
        return self.always(_negedge(clock_signal))

    def comb(self, method):
        """Decorator: register a method as combinational logic."""
        self._comb_blocks.append(method)
        return method

    def assert_always(self, clock):
        """Decorator: register a property that must hold every cycle.

        Usage:
            @self.assert_always(self.clock)
            def no_overflow():
                return self.count < 16
        """
        def decorator(func):
            self._assertions.append((clock, func))
            return func
        return decorator

    def cover(self, clock):
        """Decorator: register a coverage point.

        Usage:
            @self.cover(self.clock)
            def reaches_max():
                return self.count == 15
        """
        def decorator(func):
            self._covers.append((clock, func, [0]))
            return func
        return decorator

    def assume(self, clock):
        """Decorator: register an assumption for formal verification.

        Usage:
            @self.assume(self.clock)
            def valid_input():
                return self.enable | self.reset
        """
        def decorator(func):
            self._assumes.append((clock, func))
            return func
        return decorator

    def create_clock(self, signal, period_ns):
        """Declare a clock with the given period (ns)."""
        self._timing.append(('create_clock', signal.name, period_ns))

    def max_delay(self, from_signal, to_signal, ns):
        """Set a max delay constraint between two signals."""
        self._timing.append(('max_delay', from_signal.name, to_signal.name, ns))

    def false_path(self, from_signal, to_signal):
        """Declare a false path between two signals."""
        self._timing.append(('false_path', from_signal.name, to_signal.name))

    def to_sdc(self):
        """Generate SDC timing constraints from annotations."""
        lines = []
        for c in self._timing:
            if c[0] == 'create_clock':
                _, name, period = c
                lines.append(f'create_clock -period {period} [get_ports {name}]')
            elif c[0] == 'max_delay':
                _, fr, to, ns = c
                lines.append(f'set_max_delay {ns} -from [get_ports {fr}] -to [get_ports {to}]')
            elif c[0] == 'false_path':
                _, fr, to = c
                lines.append(f'set_false_path -from [get_ports {fr}] -to [get_ports {to}]')
        return '\n'.join(lines)

    def pipeline(self, clock, reset, width=1, *,
                 stall=None, flush=None, valid_in=None):
        """Create a pipeline with explicit stage boundaries.

        Usage:
            pipe = self.pipeline(self.clock, self.reset, width=16)
            pipe.stage(lambda: self.a + self.b)        # stage 0
            pipe.stage(lambda prev: prev * 2)          # stage 1

            @self.comb
            def output():
                self.out = pipe.result

        Multi-value stages return tuples; the next stage unpacks them:
            pipe.stage(lambda: (self.a, self.b))
            pipe.stage(lambda a, b: a + b)

        Optional *stall*, *flush*, *valid_in* add flow control.
        """
        p = _Pipeline(self, clock, reset, width,
                      stall=stall, flush=flush, valid_in=valid_in)
        if not hasattr(self, '_pipelines'):
            self._pipelines = []
        self._pipelines.append(p)
        return p

    def behavioral(self, func):
        """Decorator: register a behavioral model for fast emulation.

        The behavioral function replaces all comb/posedge blocks during
        tick() when mode='behavioral'. It is never emitted as Verilog.

        Usage:
            @self.behavioral
            def fast_model():
                self.out = self.a * self.b
        """
        self._behavioral = func
        return func

    def fsm(self, clock, reset, states):
        """Decorator: define an FSM with states and transitions.

        The decorated function receives the current state (int) and returns
        the next state. State constants are injected as local names.

        Usage:
            @self.fsm(self.clock, self.reset, states=['IDLE', 'RUN', 'DONE'])
            def ctrl(state):
                if state == IDLE:
                    if self.start:
                        return RUN
                elif state == RUN:
                    return DONE
                elif state == DONE:
                    self.done = 1
                    return IDLE
        """
        import math
        width = max(1, (len(states) - 1).bit_length())
        state_reg = Signal(width, _kind='reg', name='_fsm_state')
        next_state = Signal(width, _kind='wire', name='_fsm_next')
        object.__setattr__(self, '_fsm_state', state_reg)
        object.__setattr__(self, '_fsm_next', next_state)
        state_vals = {name: i for i, name in enumerate(states)}

        def decorator(func):
            # Inject state constants into function's globals
            func.__globals__.update(state_vals)

            def comb_wrapper():
                ns = func(int(state_reg))
                next_state._assign(ns if ns is not None else int(state_reg))

            self._comb_blocks.append(comb_wrapper)
            # Copy AST metadata for the emitter
            comb_wrapper.__wrapped__ = func
            comb_wrapper._fsm_info = {
                'states': states, 'state_vals': state_vals,
                'state_reg': state_reg, 'next_state': next_state,
                'width': width,
            }

            @self.posedge(clock)
            def fsm_update():
                if reset:
                    state_reg._assign(0)
                else:
                    state_reg._assign(int(next_state))

            return func
        return decorator

    # --- sub-module discovery ---
    def _submodules(self):
        cached = getattr(self, '_cached_submodules', None)
        if cached is not None:
            return cached
        subs = {}
        for k in dir(self):
            if k.startswith('_'):
                continue
            v = getattr(self, k)
            if isinstance(v, Module) and v is not self:
                subs[k] = v
        self._cached_submodules = subs
        return subs

    # --- signal discovery ---
    def _signals(self):
        cached = getattr(self, '_cached_signals', None)
        if cached is not None:
            return cached
        sigs = {}
        for k in dir(self):
            v = getattr(self, k)
            if isinstance(v, Signal):
                sigs[k] = v
            elif isinstance(v, Interface):
                for sig_name, sig in v._signals().items():
                    sigs[f'{k}_{sig_name}'] = sig
        self._cached_signals = sigs
        return sigs

    def _interfaces(self):
        return {k: getattr(self, k) for k in dir(self)
                if not k.startswith('_') and isinstance(getattr(self, k), Interface)}

    def _mems(self):
        cached = getattr(self, '_cached_mems', None)
        if cached is not None:
            return cached
        mems = {k: getattr(self, k) for k in dir(self)
                if isinstance(getattr(self, k), (Mem, DualPortMem, TrueDualPortMem))}
        self._cached_mems = mems
        return mems

    # --- simulation helpers (used by SimEngine) ---
    def _ensure_sim_cache(self):
        if not hasattr(self, '_sig_list'):
            self._init_sim_cache()

    def _apply_nba(self):
        """Apply non-blocking assignments (NBA region)."""
        for sig in self._sig_list:
            sig._tick()
        for mem in self._mem_list:
            mem._tick()

    def _settle_comb(self):
        """Settle combinational logic: parent → children → parent."""
        self._ensure_sim_cache()
        comb = self._comb_blocks
        nba = self._apply_nba
        for method in comb:
            method()
        nba()
        for sub_nba, sub_comb in self._sub_settle_info:
            sub_nba()
        for sub_nba, sub_comb in self._sub_settle_info:
            for method in sub_comb:
                method()
            sub_nba()
        for method in comb:
            method()
        nba()

    def _snapshot_prev(self):
        """Save current signal values for edge detection."""
        self._ensure_sim_cache()
        for sig in self._sig_list:
            sig._prev_val = sig._val
        for _, sub_sigs in self._sub_sig_lists:
            for sig in sub_sigs:
                sig._prev_val = sig._val

    def _check_edges(self):
        """Return list of (edges, method) for blocks whose sensitivity triggered."""
        self._ensure_sim_cache()
        triggered = []
        for edges, method in self._always_blocks:
            if _edges_match(edges):
                triggered.append((edges, method))
        for _, sub_always in self._sub_always_lists:
            for edges, method in sub_always:
                if _edges_match(edges):
                    triggered.append((edges, method))
        return triggered

    def _init_sim_cache(self):
        """Pre-compute lists for the simulation hot path. Called once by SimEngine."""
        self._sig_list = list(self._signals().values())
        self._mem_list = list(self._mems().values())
        subs = self._submodules()
        self._sub_settle_info = [
            (sub._apply_nba, sub._comb_blocks) for sub in subs.values()
        ]
        self._sub_sig_lists = [
            (name, list(sub._signals().values())) for name, sub in subs.items()
        ]
        self._sub_always_lists = [
            (name, sub._always_blocks) for name, sub in subs.items()
        ]
        # Init sub caches too
        for sub in subs.values():
            sub._sig_list = list(sub._signals().values())
            sub._mem_list = list(sub._mems().values())

    # --- Verilog generation ---
    def to_verilog(self, module_name=None):
        # Finalize any pipelines before emission
        for p in getattr(self, '_pipelines', []):
            p._finalize()
        from .lower import lower_module
        from .backend_verilog import emit_verilog
        return emit_verilog(lower_module(self, module_name))

    def _check_assertions(self):
        """Check assert_always properties and update cover points."""
        for _clock, func in self._assertions:
            if not func():
                raise AssertionError(f"Assertion failed: {func.__name__}")
        for _clock, func, hit in self._covers:
            if func():
                hit[0] += 1

    def coverage_report(self):
        """Return list of (name, hit_count) for all cover points."""
        return [(func.__name__, hit[0]) for _clock, func, hit in self._covers]


def _edges_match(edges):
    """Check if any edge in the list triggered (prev→current transition)."""
    for edge in edges:
        sig = edge.signal
        if edge.kind == 'posedge' and sig._prev_val == 0 and sig._val != 0:
            return True
        if edge.kind == 'negedge' and sig._prev_val != 0 and sig._val == 0:
            return True
    return False


class _Pipeline:
    """Pipeline with explicit stage boundaries.

    Each .stage() call adds a register boundary.  Stages may return a
    single value (backward-compatible) or a tuple; the next stage's
    parameter list unpacks the tuple automatically.

    Optional *stall*, *flush*, and *valid_in* signals add flow control:
    valid bits propagate with data, *stall* freezes all stages, and
    *flush* clears all valid bits.
    """

    def __init__(self, module, clock, reset, width=1, *,
                 stall=None, flush=None, valid_in=None):
        self._module = module
        self._clock = clock
        self._reset = reset
        self._width = width
        self._stall = stall
        self._flush = flush
        self._valid_in = valid_in
        self._stage_funcs = []
        self._stage_regs = []   # list of list-of-Register
        self._valid_regs = []   # list of Register|None
        self._named_stages = [] # list of _NamedStage
        self._finalized = False

    def stage(self, func_or_name, stall=None, flush=None, **fields):
        """Add a pipeline stage.

        Two calling conventions:

        Lambda-chain (existing):
            pipe.stage(lambda prev: prev + 1)

        Named-stage (new):
            pipe.stage('id_ex', stall_sig, flush_sig,
                       rs1=id_rs1_data, rd=dec.rd_addr)

        *stall* and *flush* are positional-or-keyword; all remaining
        kwargs become pipeline registers.
        """
        if callable(func_or_name):
            self._stage_funcs.append(func_or_name)
            return self
        ns = _NamedStage(self, func_or_name, stall=stall,
                         flush=flush, **fields)
        self._named_stages.append(ns)
        return ns

    # ── finalization ─────────────────────────────────────────────

    def _finalize(self):
        if self._finalized:
            return
        self._finalized = True
        # Generate deferred emit sources for named stages
        for ns in self._named_stages:
            ns._gen_emit_source()
        if not self._stage_funcs:
            return  # named-stage pipeline — nothing else to finalize

        import inspect

        funcs = self._stage_funcs
        has_ctrl = (self._stall is not None or self._flush is not None
                    or self._valid_in is not None)

        # Determine register count per stage from next stage's param count.
        # Last stage defaults to 1 register.
        for i in range(len(funcs)):
            if i + 1 < len(funcs):
                n = len(inspect.signature(funcs[i + 1]).parameters)
                n = max(n, 1)
            else:
                n = 1

            regs = []
            for j in range(n):
                reg = Register(self._width)
                reg.name = (f'_pipe_stage{i}_{j}' if n > 1
                            else f'_pipe_stage{i}')
                setattr(self._module, reg.name, reg)
                regs.append(reg)
            self._stage_regs.append(regs)

            if has_ctrl:
                vr = Register(1)
                vr.name = f'_pipe_valid{i}'
                setattr(self._module, vr.name, vr)
                self._valid_regs.append(vr)
            else:
                self._valid_regs.append(None)

        # ── posedge block ────────────────────────────────────────
        stage_regs = self._stage_regs
        valid_regs = self._valid_regs
        stall = self._stall
        flush = self._flush
        valid_in = self._valid_in
        reset = self._reset

        @self._module.posedge(self._clock)
        def _pipe_advance():
            if reset:
                for regs in stage_regs:
                    for r in regs:
                        r._val = 0
                for vr in valid_regs:
                    if vr is not None:
                        vr._val = 0
                return

            if stall is not None and int(stall):
                return

            if flush is not None and int(flush):
                for vr in valid_regs:
                    if vr is not None:
                        vr._val = 0
                return

            # Snapshot current values
            snaps = []
            for regs in stage_regs:
                if len(regs) == 1:
                    snaps.append(int(regs[0]))
                else:
                    snaps.append(tuple(int(r) for r in regs))
            vsnaps = [int(vr) if vr is not None else 1
                      for vr in valid_regs]

            # Update each stage
            for i, func in enumerate(funcs):
                if i == 0:
                    result = func()
                else:
                    prev = snaps[i - 1]
                    result = (func(*prev) if isinstance(prev, tuple)
                              else func(prev))

                regs = stage_regs[i]
                if isinstance(result, tuple):
                    for r, v in zip(regs, result):
                        r._val = int(v) & r._mask
                else:
                    regs[0]._val = int(result) & regs[0]._mask

                vr = valid_regs[i]
                if vr is not None:
                    if i == 0:
                        vr._val = (int(valid_in) if valid_in is not None
                                   else 1)
                    else:
                        vr._val = vsnaps[i - 1]

        # ── emit source for Verilog lowering ─────────────────────
        self._gen_emit_source(_pipe_advance)

    def _gen_emit_source(self, func):
        import inspect, copy, textwrap

        reset_name = (self._reset.name
                      if isinstance(self._reset, Signal) else 'reset')
        stall_name = (self._stall.name
                      if self._stall is not None
                      and isinstance(self._stall, Signal) else None)
        flush_name = (self._flush.name
                      if self._flush is not None
                      and isinstance(self._flush, Signal) else None)

        lines = ['def _pipe_advance(self):']

        # Reset
        lines.append(f'    if self.{reset_name}:')
        for regs in self._stage_regs:
            for r in regs:
                lines.append(f'        self.{r.name} = 0')
        for vr in self._valid_regs:
            if vr is not None:
                lines.append(f'        self.{vr.name} = 0')

        # Stall
        if stall_name:
            lines.append(f'    elif self.{stall_name}:')
            lines.append(f'        pass')

        # Flush
        if flush_name:
            lines.append(f'    elif self.{flush_name}:')
            for vr in self._valid_regs:
                if vr is not None:
                    lines.append(f'        self.{vr.name} = 0')

        # Normal advance — lower stage functions
        lines.append('    else:')

        # Collect signal names for bare-name → self.name rewriting
        sig_names = set()
        for name in dir(self._module):
            v = getattr(self._module, name, None)
            if isinstance(v, Signal) and not name.startswith('_pipe_'):
                sig_names.add(name)

        for i, stage_func in enumerate(self._stage_funcs):
            regs = self._stage_regs[i]
            prev_regs = self._stage_regs[i - 1] if i > 0 else []
            assign_lines = self._lower_stage(
                stage_func, i, regs, prev_regs, sig_names)
            if assign_lines is not None:
                for al in assign_lines:
                    lines.append(f'        {al}')
            else:
                # Fallback: shift register
                for r in regs:
                    if i == 0:
                        lines.append(
                            f'        self.{r.name} = self.{r.name}')
                    else:
                        pr = prev_regs[0] if len(prev_regs) == 1 else prev_regs[regs.index(r)]
                        lines.append(
                            f'        self.{r.name} = self.{pr.name}')

            # Valid propagation
            vr = self._valid_regs[i]
            if vr is not None:
                if i == 0:
                    vin = (f'self.{self._valid_in.name}'
                           if self._valid_in is not None
                           and isinstance(self._valid_in, Signal)
                           else '1')
                    lines.append(f'        self.{vr.name} = {vin}')
                else:
                    prev_vr = self._valid_regs[i - 1]
                    lines.append(
                        f'        self.{vr.name} = self.{prev_vr.name}')

        func._veripy_emit_source = '\n'.join(lines)

    @staticmethod
    def _lower_stage(stage_func, idx, regs, prev_regs, sig_names):
        """Try to lower a stage lambda to assignment source lines.

        Returns list of 'self.reg = expr' strings, or None on failure.
        """
        import inspect, copy, textwrap

        # Extract lambda AST from source
        try:
            src = textwrap.dedent(inspect.getsource(stage_func)).strip()
        except (OSError, TypeError):
            return None
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            return None
        lam = None
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Lambda):
                lam = node
                break
        if lam is None:
            return None

        # Build param → register name mapping
        param_map = {}
        params = [p.arg for p in lam.args.args]
        if prev_regs:
            if len(params) == 1 and len(prev_regs) == 1:
                param_map[params[0]] = prev_regs[0].name
            else:
                for p, r in zip(params, prev_regs):
                    param_map[p] = r.name

        # Transform the body
        body = copy.deepcopy(lam.body)
        body = _PipeASTTransformer(param_map, sig_names).visit(body)
        _ast.fix_missing_locations(body)

        # Generate assignments
        def _assign_src(reg, expr):
            tgt = _ast.Attribute(
                value=_ast.Name(id='self', ctx=_ast.Load()),
                attr=reg.name, ctx=_ast.Store())
            node = _ast.Assign(targets=[tgt], value=expr, lineno=0,
                               col_offset=0)
            _ast.fix_missing_locations(node)
            return _ast.unparse(node)

        if isinstance(body, _ast.Tuple):
            return [_assign_src(r, e)
                    for r, e in zip(regs, body.elts)]
        return [_assign_src(regs[0], body)]

    # ── public API ───────────────────────────────────────────────

    @property
    def result(self):
        """Output of the last pipeline stage."""
        self._finalize()
        regs = self._stage_regs[-1]
        return regs[0] if len(regs) == 1 else tuple(regs)

    @property
    def valid_out(self):
        """Valid bit from the last pipeline stage."""
        self._finalize()
        vr = self._valid_regs[-1]
        if vr is None:
            raise AttributeError(
                "No valid tracking — pass stall, flush, or valid_in")
        return vr

    def behavioral(self):
        """Run all stages sequentially — no registers, no clocking."""
        val = self._stage_funcs[0]()
        for func in self._stage_funcs[1:]:
            val = func(*val) if isinstance(val, tuple) else func(val)
        return val


class _NamedStage:
    """A named pipeline stage with auto-generated registers and clocking.

    Created via ``pipe.stage('name', stall=..., flush=..., field=source)``.
    Each *field=source* pair becomes a ``Register`` on the parent module
    (named ``{stage}_{field}``).  Width is inferred from the source signal.

    On each posedge:
      reset  → zero all registers
      stall  → hold
      flush  → zero all registers
      else   → latch ``int(source)`` into each register
    """

    def __init__(self, pipeline, name, *, stall=None, flush=None, **fields):
        self._pipeline = pipeline
        self._name = name
        self._stall = stall
        self._flush = flush
        self._regs = {}
        self._field_sources = []  # [(field_name, source_signal)]

        module = pipeline._module
        field_pairs = []  # (reg, source) for posedge closure

        for fname, source in fields.items():
            width = getattr(source, '_width', None) or getattr(source, 'width', 1)
            reg = Register(width)
            reg.name = f'{name}_{fname}'
            setattr(module, reg.name, reg)
            self._regs[fname] = reg
            field_pairs.append((reg, source))
            self._field_sources.append((fname, source))

        # ── posedge block ────────────────────────────────────────
        clock = pipeline._clock
        reset = pipeline._reset
        stall_sig = stall
        flush_sig = flush

        @module.posedge(clock)
        def _advance():
            if reset:
                for r, _ in field_pairs:
                    r._assign(0)
                return
            if stall_sig is not None and int(stall_sig):
                return
            if flush_sig is not None and int(flush_sig):
                for r, _ in field_pairs:
                    r._assign(0)
                return
            for r, src in field_pairs:
                r._assign(int(src) & r._mask)

        self._advance_func = _advance

    def _resolve_name(self, sig):
        """Resolve a signal's Verilog name, searching the module if needed."""
        name = getattr(sig, 'name', '') or ''
        if name:
            return name
        module = self._pipeline._module
        for attr in dir(module):
            if getattr(module, attr, None) is sig:
                return attr
        return ''

    def _gen_emit_source(self):
        """Generate Verilog emit source — called at to_verilog() time."""
        reset = self._pipeline._reset
        rn = self._resolve_name(reset) or 'reset'
        stall, flush = self._stall, self._flush
        name = self._name
        resolve = self._resolve_name

        def _src(sig):
            """Render a signal/expr as a Python expression for emit source."""
            if isinstance(sig, _Expr):  return sig._to_emit_python(resolve)
            if isinstance(sig, _SliceProxy):
                n = resolve(sig._signal)
                return f'self.{n}[{sig._hi}:{sig._lo}]' if sig._hi != sig._lo else f'self.{n}[{sig._lo}]'
            return f'self.{resolve(sig)}'

        lines = [f'def _{name}_advance(self):']
        lines.append(f'    if self.{rn}:')
        for fname, _ in self._field_sources:
            lines.append(f'        self.{name}_{fname} = 0')

        if stall is not None:
            lines.append(f'    elif {_src(stall)}:')
            lines.append('        pass')

        if flush is not None:
            lines.append(f'    elif {_src(flush)}:')
            for fname, _ in self._field_sources:
                lines.append(f'        self.{name}_{fname} = 0')

        lines.append('    else:')
        for fname, src in self._field_sources:
            lines.append(f'        self.{name}_{fname} = {_src(src)}')

        self._advance_func._veripy_emit_source = '\n'.join(lines)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        regs = object.__getattribute__(self, '_regs')
        if name in regs:
            return regs[name]
        raise AttributeError(
            f"Stage {self._name!r} has no field {name!r}")


class _PipeASTTransformer(_ast.NodeTransformer):
    """Rewrite a pipeline stage lambda body for Verilog emission."""

    def __init__(self, param_map, sig_names):
        self.param_map = param_map
        self.sig_names = sig_names

    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, _ast.Name) and node.func.id == 'int'
                and len(node.args) == 1):
            return node.args[0]
        return node

    def visit_Name(self, node):
        if node.id in self.param_map:
            return _ast.Attribute(
                value=_ast.Name(id='self', ctx=_ast.Load()),
                attr=self.param_map[node.id], ctx=node.ctx)
        if node.id in self.sig_names:
            return _ast.Attribute(
                value=_ast.Name(id='self', ctx=_ast.Load()),
                attr=node.id, ctx=node.ctx)
        return node
