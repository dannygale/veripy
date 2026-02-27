"""Module base class: defines the structure for simulation and Verilog emission."""

from .signal import Signal, Mem, Edge, SensitivityList, Interface, posedge as _posedge


class Module:
    """Base class for hardware modules.

    Subclasses define signals in __init__, then register combinational
    and sequential blocks via @self.comb, @self.posedge, @self.negedge,
    or @self.always decorators.
    Sub-modules are any Module-typed attributes (self.alu = ALU(...)).
    """

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
