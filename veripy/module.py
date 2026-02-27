"""Module base class: defines the structure for simulation and Verilog emission."""

from .signal import Signal, SignalArray, Mem


class Module:
    """Base class for hardware modules.

    Subclasses define signals in __init__, then register combinational
    and sequential blocks via @self.comb and @self.posedge decorators.
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
        self._posedge_blocks = []
        self._comb_blocks = []
        self._params = params or {}  # {'n': 8} → parameter n = 8
        # Auto-name signals and signal arrays from attribute names
        for attr in dir(self):
            val = getattr(self, attr)
            if isinstance(val, Signal) and not val.name:
                val.name = attr
            elif isinstance(val, SignalArray) and not val.name:
                val._name_elements(attr)
            elif isinstance(val, Mem) and not val.name:
                val.name = attr

    def posedge(self, clock_signal):
        """Decorator: register a method as a posedge-triggered always block."""
        def decorator(method):
            self._posedge_blocks.append((clock_signal, method))
            return method
        return decorator

    def comb(self, method):
        """Decorator: register a method as combinational logic."""
        self._comb_blocks.append(method)
        return method

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

    # --- simulation ---
    def _signals(self):
        sigs = {}
        for k in dir(self):
            v = getattr(self, k)
            if isinstance(v, Signal):
                sigs[k] = v
            elif isinstance(v, SignalArray):
                for i, s in enumerate(v):
                    sigs[f'{k}[{i}]'] = s
        return sigs

    def _mems(self):
        return {k: getattr(self, k) for k in dir(self)
                if isinstance(getattr(self, k), Mem)}

    def _tick_signals(self):
        for sig in self._signals().values():
            sig._tick()
        for mem in self._mems().values():
            mem._tick()

    def _settle_comb(self):
        """Settle combinational logic: parent → children → parent."""
        subs = self._submodules()
        # Parent comb drives child inputs
        for method in self._comb_blocks:
            method()
        self._tick_signals()
        for sub in subs.values():
            sub._tick_signals()
        # Child comb computes outputs
        for sub in subs.values():
            for method in sub._comb_blocks:
                method()
            sub._tick_signals()
        # Parent comb reads child outputs
        for method in self._comb_blocks:
            method()
        self._tick_signals()

    def tick(self):
        """Advance one clock cycle.

        1. Settle comb (so posedge sees current values)
        2. Posedge (parent + children capture settled values)
        3. Apply register updates
        4. Settle comb again (propagate new register values)
        """
        subs = self._submodules()

        # Phase 1: settle comb before clock edge
        self._settle_comb()

        # Phase 2: posedge (parent + children)
        for _clk, method in self._posedge_blocks:
            method()
        for sub in subs.values():
            for _clk, method in sub._posedge_blocks:
                method()

        # Phase 3: apply register updates
        self._tick_signals()
        for sub in subs.values():
            sub._tick_signals()

        # Phase 4: settle comb with new register values
        self._settle_comb()

    def simulate(self, cycles):
        for _ in range(cycles):
            self.tick()

    # --- Verilog generation ---
    def to_verilog(self, module_name=None):
        from .emit_verilog import VerilogEmitter
        return VerilogEmitter(self, module_name).emit()
