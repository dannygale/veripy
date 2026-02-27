"""Module base class: defines the structure for simulation and Verilog emission."""

from .signal import Signal, Mem, Edge, SensitivityList, Interface, Register, posedge as _posedge
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
        if not name.startswith('_'):
            try:
                existing = object.__getattribute__(self, name)
                if isinstance(existing, Signal):
                    existing._assign(value)
                    return
            except AttributeError:
                pass
        object.__setattr__(self, name, value)

    def __init__(self, params=None):
        self._always_blocks = []   # [(edges_list, method), ...]
        self._comb_blocks = []
        self._assertions = []      # [(clock_signal, func), ...]
        self._covers = []          # [(clock_signal, func, hit), ...]
        self._timing = []          # [(constraint_type, kwargs), ...]
        if params is not None:
            self._params = params
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
            self._covers.append((clock, func, [False]))
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

    def pipeline(self, clock, reset, width=1):
        """Create a pipeline with explicit stage boundaries.

        Usage:
            pipe = self.pipeline(self.clock, self.reset, width=16)
            pipe.stage(lambda: self.a + self.b)        # stage 0
            pipe.stage(lambda prev: prev * 2)          # stage 1

            @self.comb
            def output():
                self.out = pipe.result
        """
        p = _Pipeline(self, clock, reset, width)
        if not hasattr(self, '_pipelines'):
            self._pipelines = []
        self._pipelines.append(p)
        return p

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
        subs = {}
        for k in dir(self):
            if k.startswith('_'):
                continue
            v = getattr(self, k)
            if isinstance(v, Module) and v is not self:
                subs[k] = v
        return subs

    # --- signal discovery ---
    def _signals(self):
        sigs = {}
        for k in dir(self):
            v = getattr(self, k)
            if isinstance(v, Signal):
                sigs[k] = v
            elif isinstance(v, Interface):
                for sig_name, sig in v._signals().items():
                    sigs[f'{k}_{sig_name}'] = sig
        return sigs

    def _interfaces(self):
        return {k: getattr(self, k) for k in dir(self)
                if not k.startswith('_') and isinstance(getattr(self, k), Interface)}

    def _mems(self):
        return {k: getattr(self, k) for k in dir(self)
                if isinstance(getattr(self, k), Mem)}

    # --- simulation helpers (used by SimEngine) ---
    def _apply_nba(self):
        """Apply non-blocking assignments (NBA region)."""
        for sig in self._signals().values():
            sig._tick()
        for mem in self._mems().values():
            mem._tick()

    def _settle_comb(self):
        """Settle combinational logic: parent → children → parent."""
        subs = self._submodules()
        for method in self._comb_blocks:
            method()
        self._apply_nba()
        for sub in subs.values():
            sub._apply_nba()
        for sub in subs.values():
            for method in sub._comb_blocks:
                method()
            sub._apply_nba()
        for method in self._comb_blocks:
            method()
        self._apply_nba()

    def _snapshot_prev(self):
        """Save current signal values for edge detection."""
        for sig in self._signals().values():
            sig._prev_val = sig._val
        for sub in self._submodules().values():
            for sig in sub._signals().values():
                sig._prev_val = sig._val

    def _check_edges(self):
        """Return list of (edges, method) for blocks whose sensitivity triggered."""
        triggered = []
        for edges, method in self._always_blocks:
            if _edges_match(edges):
                triggered.append((edges, method))
        for sub in self._submodules().values():
            for edges, method in sub._always_blocks:
                if _edges_match(edges):
                    triggered.append((edges, method))
        return triggered

    # --- Verilog generation ---
    def to_verilog(self, module_name=None):
        # Finalize any pipelines before emission
        for p in getattr(self, '_pipelines', []):
            p._finalize()
        from .emit_verilog import VerilogEmitter
        return VerilogEmitter(self, module_name).emit()

    # --- convenience for direct sim / unit tests ---
    def tick(self):
        """Advance one clock cycle. Fires all always blocks unconditionally.

        For proper edge-driven simulation, use SimEngine instead.
        """
        subs = self._submodules()
        self._settle_comb()
        for _edges, method in self._always_blocks:
            method()
        for sub in subs.values():
            for _edges, method in sub._always_blocks:
                method()
        self._apply_nba()
        for sub in subs.values():
            sub._apply_nba()
        self._settle_comb()
        self._check_assertions()

    def simulate(self, cycles):
        """Run tick() for N cycles."""
        for _ in range(cycles):
            self.tick()

    def _check_assertions(self):
        """Check assert_always properties and update cover points."""
        for _clock, func in self._assertions:
            if not func():
                raise AssertionError(f"Assertion failed: {func.__name__}")
        for _clock, func, hit in self._covers:
            if func():
                hit[0] = True


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

    Each .stage() call adds a register boundary. The framework creates
    registers and wires them with posedge blocks automatically.
    """

    def __init__(self, module, clock, reset, width):
        self._module = module
        self._clock = clock
        self._reset = reset
        self._width = width
        self._stages = []      # list of (register, func)
        self._finalized = False

    def stage(self, func):
        """Add a pipeline stage.

        func receives no args for the first stage, or the previous
        stage's registered output for subsequent stages.
        """
        idx = len(self._stages)
        reg = Register(self._width)
        reg.name = f'_pipe_stage{idx}'
        setattr(self._module, f'_pipe_stage{idx}', reg)
        self._stages.append((reg, func))
        return self

    def _finalize(self):
        if self._finalized:
            return
        self._finalized = True
        stages = self._stages
        clock = self._clock
        reset = self._reset

        @self._module.posedge(clock)
        def _pipe_advance():
            if reset:
                for reg, _ in stages:
                    reg._val = 0
            else:
                # Snapshot current values, then update — gives real latency
                vals = [int(reg) for reg, _ in stages]
                for i, (reg, func) in enumerate(stages):
                    if i == 0:
                        reg._val = int(func())
                    else:
                        reg._val = int(func(vals[i - 1]))

        # Generate emitter-compatible source for the advance block
        reset_name = reset.name if isinstance(reset, Signal) else 'reset'
        lines = ['def _pipe_advance(self):']
        lines.append(f'    if self.{reset_name}:')
        for i in range(len(stages)):
            lines.append(f'        self._pipe_stage{i} = 0')
        lines.append('    else:')
        for i in range(len(stages)):
            if i == 0:
                lines.append(f'        self._pipe_stage0 = self._pipe_stage0')
            else:
                lines.append(f'        self._pipe_stage{i} = self._pipe_stage{i - 1}')
        _pipe_advance._veripy_emit_source = '\n'.join(lines)

    @property
    def result(self):
        """Output of the last pipeline stage."""
        self._finalize()
        return self._stages[-1][0]
