"""Signal types: the core value-carrying abstraction.

For simulation, signals hold integer values and operators return ints.
For Verilog generation, the AST of methods using signals is parsed directly.
"""


class Signal:
    """A named, width-constrained hardware value."""

    def __init__(self, width=1, *, name='', reset=0, _kind='wire'):
        self.width = width
        self.name = name
        self.reset = reset
        self._kind = _kind          # 'input', 'output', 'reg', 'wire'
        self._mask = (1 << width) - 1
        self._val = reset & self._mask
        self._next = None           # pending non-blocking assignment

    # --- value access ---
    def _int(self, other):
        if isinstance(other, Signal):
            return other._val
        return int(other)

    @property
    def val(self):
        return self._val

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
    def __rshift__(self, o):    return self._val >> self._int(o)
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
    def __repr__(self):         return f"Signal({self.name}={self._val}, w={self.width})"

    # --- non-blocking assignment: <<= ---
    def __ilshift__(self, value):
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
        return self

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
        return (self._val >> key) & 1

    def __setitem__(self, key, value):
        # _SliceProxy.__ilshift__ already scheduled the update; accept silently.
        pass


class _SliceProxy:
    """Proxy for bit-slice access. Supports read (int conversion) and write (<<=)."""

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

    def __eq__(self, o):
        return self._val == (o._val if isinstance(o, (Signal, _SliceProxy)) else int(o))

    def __ilshift__(self, value):
        """Partial write: schedule update to just this bit range."""
        v = value._val if isinstance(value, Signal) else int(value)
        v &= self._mask
        # Clear the target bits, set new value
        full_mask = self._signal._mask
        clear = full_mask & ~(self._mask << self._lo)
        current = self._signal._val if self._signal._next is None else self._signal._next
        self._signal._next = (current & clear) | (v << self._lo)
        return self


def Input(width=1):
    return Signal(width, _kind='input')

def Output(width=1):
    return Signal(width, _kind='output')

def Register(width=1, reset=0):
    return Signal(width, reset=reset, _kind='reg')


class SignalArray(list):
    """A fixed-size array of signals. Emits as Verilog memory/register array."""

    def __init__(self, depth, width=1, *, reset=0, _kind='reg'):
        self.depth = depth
        self.width = width
        self.name = ''
        signals = [Signal(width, reset=reset, _kind=_kind) for _ in range(depth)]
        super().__init__(signals)

    def _name_elements(self, name):
        self.name = name
        for i, s in enumerate(self):
            s.name = f'{name}[{i}]'
