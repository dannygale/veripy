"""Module base class: defines the structure for simulation and Verilog emission."""

from .signal import Signal, SignalArray


class Module:
    """Base class for hardware modules.

    Subclasses define signals in __init__, then register combinational
    and sequential blocks via @self.comb and @self.posedge decorators.
    """

    def __init__(self):
        self._posedge_blocks = []
        self._comb_blocks = []
        # Auto-name signals and signal arrays from attribute names
        for attr in dir(self):
            val = getattr(self, attr)
            if isinstance(val, Signal) and not val.name:
                val.name = attr
            elif isinstance(val, SignalArray) and not val.name:
                val._name_elements(attr)

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

    def tick(self):
        """Advance one clock cycle: posedge → apply registers → eval comb → settle."""
        for _clk, method in self._posedge_blocks:
            method()
        for sig in self._signals().values():
            sig._tick()
        for method in self._comb_blocks:
            method()
        # Settle combinational assignments
        for sig in self._signals().values():
            sig._tick()

    def simulate(self, cycles):
        for _ in range(cycles):
            self.tick()

    # --- Verilog generation ---
    def to_verilog(self, module_name=None):
        from .emit_verilog import VerilogEmitter
        return VerilogEmitter(self, module_name).emit()
