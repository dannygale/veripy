"""Signal types: the core value-carrying abstraction.

For simulation, signals hold integer values and operators return ints.
For Verilog generation, the AST of methods using signals is parsed directly.
"""


# --- Edge types for sensitivity lists ---

class Edge:
    """A single edge specifier: posedge(sig) or negedge(sig)."""
    __slots__ = ('signal', 'kind')

    def __init__(self, signal, kind):
        self.signal = signal
        self.kind = kind  # 'posedge' or 'negedge'

    def __or__(self, other):
        if isinstance(other, Edge):
            return SensitivityList([self, other])
        if isinstance(other, SensitivityList):
            return SensitivityList([self] + other.edges)
        return NotImplemented


class SensitivityList:
    """Multiple edges combined with |."""
    __slots__ = ('edges',)

    def __init__(self, edges):
        self.edges = list(edges)

    def __or__(self, other):
        if isinstance(other, Edge):
            return SensitivityList(self.edges + [other])
        if isinstance(other, SensitivityList):
            return SensitivityList(self.edges + other.edges)
        return NotImplemented


def posedge(signal):
    """Create a posedge specifier for use in sensitivity lists."""
    return Edge(signal, 'posedge')

def negedge(signal):
    """Create a negedge specifier for use in sensitivity lists."""
    return Edge(signal, 'negedge')


class Signal:
    """A named, width-constrained hardware value."""

    def __init__(self, width=1, *, name='', reset=0, _kind='wire'):
        from .parameter import is_param
        self._width_param = width if is_param(width) else None
        if self._width_param is not None:
            width = width.default
        self.width = width
        self.name = name
        self.reset = reset
        self._kind = _kind          # 'input', 'output', 'reg', 'wire'
        self._mask = (1 << width) - 1
        self._val = reset & self._mask
        self._prev_val = self._val  # for edge detection
        self._next = None           # pending non-blocking assignment

    def _clone(self, param_values):
        """Create a resolved copy with concrete width."""
        if self._width_param is not None:
            width = self._width_param.resolve(param_values)
        else:
            width = self.width
        return Signal(width, name=self.name, reset=self.reset, _kind=self._kind)

    # --- value access ---
    def _int(self, other):
        if isinstance(other, Signal):
            return other._val
        return int(other)

    @property
    def val(self):
        return self._val

    def set(self, value):
        """Immediately set the signal value (for testbenches)."""
        self._val = self._int(value) & self._mask

    # --- simulation operators (return plain ints) ---
    def __add__(self, o):       return (self._val + self._int(o)) & self._mask
    def __radd__(self, o):      return (self._int(o) + self._val) & self._mask
    def __sub__(self, o):       return (self._val - self._int(o)) & self._mask
    def __rsub__(self, o):      return (self._int(o) - self._val) & self._mask
    def __and__(self, o):       return self._val & self._int(o)
    def __rand__(self, o):      return self._int(o) & self._val
    def __or__(self, o):        return self._val | self._int(o)
    def __ror__(self, o):       return self._int(o) | self._val
    def __xor__(self, o):       return self._val ^ self._int(o)
    def __rxor__(self, o):      return self._int(o) ^ self._val
    def __lshift__(self, o):    return (self._val << self._int(o)) & self._mask
    def __rlshift__(self, o):   return self._int(o) << self._val
    def __rshift__(self, o):    return self._val >> self._int(o)
    def __rrshift__(self, o):   return self._int(o) >> self._val
    def __invert__(self):       return (~self._val) & self._mask
    def __neg__(self):          return (-self._val) & self._mask
    def __eq__(self, o):        return self._val == self._int(o)
    def __ne__(self, o):        return self._val != self._int(o)
    def __lt__(self, o):        return self._val < self._int(o)
    def __le__(self, o):        return self._val <= self._int(o)
    def __gt__(self, o):        return self._val > self._int(o)
    def __ge__(self, o):        return self._val >= self._int(o)
    def __bool__(self):         return self._val != 0
    def __int__(self):          return self._val
    def __index__(self):        return self._val
    def __hash__(self):         return id(self)
    def __repr__(self):         return f"Signal({self.name}={self._val}, w={self.width})"

    # --- non-blocking assignment ---
    def _assign(self, value):
        """Schedule next-cycle update (non-blocking assignment)."""
        if isinstance(value, list):
            v, shift = 0, 0
            for p in value:
                w = p.width if isinstance(p, Signal) else 1
                v |= (int(p) & ((1 << w) - 1)) << shift
                shift += w
            self._next = v & self._mask
        else:
            self._next = self._int(value) & self._mask

    def _tick(self):
        """Apply pending non-blocking assignment."""
        if self._next is not None:
            self._val = self._next
            self._next = None

    # --- bit slicing ---
    def __getitem__(self, key):
        if isinstance(key, slice):
            hi = key.start if key.start is not None else self.width - 1
            lo = key.stop if key.stop is not None else 0
            return _SliceProxy(self, hi, lo)
        return _SliceProxy(self, key, key)

    def __setitem__(self, key, value):
        if isinstance(key, slice):
            self[key]._assign(value)
        else:
            # single bit
            self._next = ((self._val if self._next is None else self._next)
                          & ~(1 << key)) | ((int(value) & 1) << key)


class _SliceProxy:
    """Proxy for bit-slice access. Supports read (int conversion) and write (=)."""

    def __init__(self, signal, hi, lo):
        self._signal = signal
        self._hi = hi
        self._lo = lo
        self._bits = hi - lo + 1
        self._mask = (1 << self._bits) - 1

    @property
    def _val(self):
        return (self._signal._val >> self._lo) & self._mask

    def __int__(self):
        return self._val

    def __index__(self):
        return self._val

    def __bool__(self):
        return self._val != 0

    def __getitem__(self, key):
        if isinstance(key, slice):
            hi = key.start if key.start is not None else self._bits - 1
            lo = key.stop if key.stop is not None else 0
            return _SliceProxy(self._signal, self._lo + hi, self._lo + lo)
        return (self._val >> key) & 1

    def _int(self, o):
        return o._val if isinstance(o, (Signal, _SliceProxy)) else int(o)

    # --- arithmetic / bitwise (return plain ints, same as Signal) ---
    def __add__(self, o):       return self._val + self._int(o)
    def __radd__(self, o):      return self._int(o) + self._val
    def __sub__(self, o):       return self._val - self._int(o)
    def __rsub__(self, o):      return self._int(o) - self._val
    def __and__(self, o):       return self._val & self._int(o)
    def __rand__(self, o):      return self._int(o) & self._val
    def __or__(self, o):        return self._val | self._int(o)
    def __ror__(self, o):       return self._int(o) | self._val
    def __xor__(self, o):       return self._val ^ self._int(o)
    def __rxor__(self, o):      return self._int(o) ^ self._val
    def __lshift__(self, o):    return self._val << self._int(o)
    def __rlshift__(self, o):   return self._int(o) << self._val
    def __rshift__(self, o):    return self._val >> self._int(o)
    def __rrshift__(self, o):   return self._int(o) >> self._val
    def __invert__(self):       return ~self._val & self._mask
    def __neg__(self):          return (-self._val) & self._mask
    def __eq__(self, o):        return self._val == self._int(o)
    def __ne__(self, o):        return self._val != self._int(o)
    def __lt__(self, o):        return self._val < self._int(o)
    def __le__(self, o):        return self._val <= self._int(o)
    def __gt__(self, o):        return self._val > self._int(o)
    def __ge__(self, o):        return self._val >= self._int(o)

    def _assign(self, value):
        """Partial write: schedule update to just this bit range."""
        v = value._val if isinstance(value, Signal) else int(value)
        v &= self._mask
        # Clear the target bits, set new value
        full_mask = self._signal._mask
        clear = full_mask & ~(self._mask << self._lo)
        current = self._signal._val if self._signal._next is None else self._signal._next
        self._signal._next = (current & clear) | (v << self._lo)


def Input(width=1):
    return Signal(width, _kind='input')

def Output(width=1):
    return Signal(width, _kind='output')

def Register(width=1, reset=0):
    return Signal(width, reset=reset, _kind='reg')


class Interface:
    """Base class for reusable signal bundles.

    Define signals using Input/Output (like Module ports). Signals are
    accessed as self.bus.signal in Python and emitted as bus_signal in Verilog.

    Static usage:
        class AXILite(Interface):
            awaddr  = Input(32)
            awvalid = Input(1)
            awready = Output(1)

    Parameterized usage:
        class AXILite(Interface):
            def __init__(self, data_width=32, addr_width=32):
                self.awaddr  = Input(addr_width)
                self.awvalid = Input(1)
                self.wdata   = Output(data_width)
                super().__init__()
    """

    @staticmethod
    def _sig_def(val):
        """Extract (kind, width) from a Signal or legacy tuple, or return None."""
        if isinstance(val, Signal) and val._kind in ('input', 'output'):
            return val._kind, val._width_param if val._width_param is not None else val.width
        if isinstance(val, tuple) and len(val) == 2 and val[0] in ('input', 'output'):
            return val
        return None

    def __init__(self):
        defs = {}
        for name in dir(type(self)):
            d = self._sig_def(getattr(type(self), name))
            if d:
                defs[name] = d
        for name in list(vars(self)):
            d = self._sig_def(vars(self)[name])
            if d:
                defs[name] = d
        for name, (kind, width) in defs.items():
            object.__setattr__(self, name, Signal(width, _kind=kind, name=name))

    def __setattr__(self, name, value):
        try:
            existing = object.__getattribute__(self, name)
            if isinstance(existing, Signal):
                existing._assign(value)
                return
        except AttributeError:
            pass
        object.__setattr__(self, name, value)

    def _signals(self):
        return {k: v for k in dir(self)
                if not k.startswith('_') and isinstance((v := getattr(self, k)), Signal)}

    @staticmethod
    def _match(lhs, rhs):
        """Return [(signal_name, 'l2r'|'r2l'|'fwd'), ...] for matching signals."""
        same_type = type(lhs) is type(rhs)
        if not same_type:
            if hasattr(lhs, '_connects_to') and not isinstance(rhs, lhs._connects_to):
                raise TypeError(f"Cannot connect {type(lhs).__name__} to {type(rhs).__name__}")
            if hasattr(rhs, '_connects_to') and not isinstance(lhs, rhs._connects_to):
                raise TypeError(f"Cannot connect {type(rhs).__name__} to {type(lhs).__name__}")
        pairs = []
        l_sigs, r_sigs = lhs._signals(), rhs._signals()
        for name, r_sig in r_sigs.items():
            l_sig = l_sigs.get(name)
            if l_sig is None:
                continue
            if r_sig._kind == l_sig._kind:
                if same_type:
                    pairs.append((name, 'fwd'))
                else:
                    raise TypeError(
                        f"Signal '{name}' is {r_sig._kind} on both "
                        f"{type(lhs).__name__} and {type(rhs).__name__}")
            elif r_sig._kind == 'output':
                pairs.append((name, 'r2l'))
            else:
                pairs.append((name, 'l2r'))
        if not pairs:
            raise TypeError(
                f"No matching signals between {type(lhs).__name__} and {type(rhs).__name__}")
        return pairs



class Mem:
    """Memory array: indexed read (combinational) and write (scheduled).

    Simulation: mem[addr] reads, mem.write(addr, data) schedules a write.
    Verilog: emits reg [W-1:0] name [0:D-1], read as name[addr],
             write as name[addr] <= data inside always @(posedge).
    """

    def __init__(self, depth, width=1):
        from .parameter import is_param
        self._width_param = width if is_param(width) else None
        self._depth_param = depth if is_param(depth) else None
        if self._width_param is not None:
            width = width.default
        if self._depth_param is not None:
            depth = depth.default
        self.depth = depth
        self.width = width
        self.name = ''
        self._mask = (1 << width) - 1
        self._data = [0] * depth
        self._pending_write = None  # (addr, value)

    def _clone(self, param_values):
        """Create a resolved copy with concrete dimensions."""
        from .parameter import is_param
        width = self._width_param.resolve(param_values) if self._width_param else self.width
        depth = self._depth_param.resolve(param_values) if self._depth_param else self.depth
        m = Mem(depth, width)
        m.name = self.name
        return m

    def __getitem__(self, addr):
        """Combinational read."""
        a = addr._val if isinstance(addr, Signal) else int(addr)
        return self._data[a % self.depth]

    def write(self, addr, value):
        """Schedule a write (applied on next tick)."""
        a = addr._val if isinstance(addr, Signal) else int(addr)
        v = value._val if isinstance(value, Signal) else int(value)
        self._pending_write = (a % self.depth, v & self._mask)

    def _tick(self):
        if self._pending_write is not None:
            addr, val = self._pending_write
            self._data[addr] = val
            self._pending_write = None
