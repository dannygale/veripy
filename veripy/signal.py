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


def _iv(x):
    """Get int value from Signal, _Expr, _SliceProxy, or int."""
    return int(x)

def _w(x, default=1):
    """Get width of an operand."""
    return getattr(x, '_width', None) or getattr(x, 'width', default)


class _Expr:
    """Lazy expression returned by Signal/Register operators.

    Evaluates its function on each ``int()`` / ``bool()`` call, so it
    always reflects the current signal values.  Carries ``_width`` for
    width inference in pipeline stages and other contexts.
    """
    __slots__ = ('_fn', '_width', '_mask', '_op', '_args')

    def __init__(self, fn, width, op=None, args=()):
        self._fn = fn
        self._width = width
        self._mask = (1 << width) - 1
        self._op = op
        self._args = args

    @property
    def width(self):    return self._width
    @property
    def _val(self):     return self._fn()
    @property
    def name(self):     return ''

    def _to_emit_python(self, resolve):
        """Render as Python expression string for emit source (e.g. 'self.a + 4')."""
        def _r(a):
            if isinstance(a, _Expr):    return a._to_emit_python(resolve)
            if isinstance(a, _SliceProxy):
                n = resolve(a._signal)
                return f'self.{n}[{a._hi}:{a._lo}]' if a._hi != a._lo else f'self.{n}[{a._lo}]'
            if isinstance(a, Signal):   return f'self.{resolve(a)}'
            return str(int(a))
        if self._op == '~':   return f'(~{_r(self._args[0])})'
        if self._op == 'neg': return f'(-{_r(self._args[0])})'
        l, r = self._args
        return f'({_r(l)} {self._op} {_r(r)})'

    def __int__(self):   return self._fn()
    def __index__(self): return self._fn()
    def __bool__(self):  return self._fn() != 0
    def __hash__(self):  return id(self)
    def __repr__(self):  return f"_Expr(w={self._width}, val={self._fn()})"

    # --- operators (return new _Expr) ---
    def __add__(self, o):       return _Expr(lambda: (self._fn() + _iv(o)) & self._mask, self._width, '+', (self, o))
    def __radd__(self, o):      return _Expr(lambda: (_iv(o) + self._fn()) & self._mask, self._width, '+', (o, self))
    def __sub__(self, o):       return _Expr(lambda: (self._fn() - _iv(o)) & self._mask, self._width, '-', (self, o))
    def __rsub__(self, o):      return _Expr(lambda: (_iv(o) - self._fn()) & self._mask, self._width, '-', (o, self))
    def __mul__(self, o):       return _Expr(lambda: self._fn() * _iv(o), self._width + _w(o), '*', (self, o))
    def __rmul__(self, o):      return _Expr(lambda: _iv(o) * self._fn(), _w(o) + self._width, '*', (o, self))
    def __and__(self, o):       return _Expr(lambda: self._fn() & _iv(o), max(self._width, _w(o)), '&', (self, o))
    def __rand__(self, o):      return _Expr(lambda: _iv(o) & self._fn(), max(self._width, _w(o)), '&', (o, self))
    def __or__(self, o):        return _Expr(lambda: self._fn() | _iv(o), max(self._width, _w(o)), '|', (self, o))
    def __ror__(self, o):       return _Expr(lambda: _iv(o) | self._fn(), max(self._width, _w(o)), '|', (o, self))
    def __xor__(self, o):       return _Expr(lambda: self._fn() ^ _iv(o), max(self._width, _w(o)), '^', (self, o))
    def __rxor__(self, o):      return _Expr(lambda: _iv(o) ^ self._fn(), max(self._width, _w(o)), '^', (o, self))
    def __lshift__(self, o):    return _Expr(lambda: (self._fn() << _iv(o)) & self._mask, self._width, '<<', (self, o))
    def __rlshift__(self, o):   return _Expr(lambda: _iv(o) << self._fn(), _w(o), '<<', (o, self))
    def __rshift__(self, o):    return _Expr(lambda: self._fn() >> _iv(o), self._width, '>>', (self, o))
    def __rrshift__(self, o):   return _Expr(lambda: _iv(o) >> self._fn(), _w(o), '>>', (o, self))
    def __invert__(self):       return _Expr(lambda: (~self._fn()) & self._mask, self._width, '~', (self,))
    def __neg__(self):          return _Expr(lambda: (-self._fn()) & self._mask, self._width, 'neg', (self,))
    def __eq__(self, o):        return _Expr(lambda: int(self._fn() == _iv(o)), 1, '==', (self, o))
    def __ne__(self, o):        return _Expr(lambda: int(self._fn() != _iv(o)), 1, '!=', (self, o))
    def __lt__(self, o):        return _Expr(lambda: int(self._fn() < _iv(o)), 1, '<', (self, o))
    def __le__(self, o):        return _Expr(lambda: int(self._fn() <= _iv(o)), 1, '<=', (self, o))
    def __gt__(self, o):        return _Expr(lambda: int(self._fn() > _iv(o)), 1, '>', (self, o))
    def __ge__(self, o):        return _Expr(lambda: int(self._fn() >= _iv(o)), 1, '>=', (self, o))


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

    # --- simulation operators (return _Expr for lazy evaluation) ---
    def __add__(self, o):       return _Expr(lambda: (self._val + _iv(o)) & self._mask, self.width, '+', (self, o))
    def __radd__(self, o):      return _Expr(lambda: (_iv(o) + self._val) & self._mask, self.width, '+', (o, self))
    def __sub__(self, o):       return _Expr(lambda: (self._val - _iv(o)) & self._mask, self.width, '-', (self, o))
    def __rsub__(self, o):      return _Expr(lambda: (_iv(o) - self._val) & self._mask, self.width, '-', (o, self))
    def __mul__(self, o):       return _Expr(lambda: self._val * _iv(o), self.width + _w(o), '*', (self, o))
    def __rmul__(self, o):      return _Expr(lambda: _iv(o) * self._val, _w(o) + self.width, '*', (o, self))
    def __and__(self, o):       return _Expr(lambda: self._val & _iv(o), max(self.width, _w(o)), '&', (self, o))
    def __rand__(self, o):      return _Expr(lambda: _iv(o) & self._val, max(self.width, _w(o)), '&', (o, self))
    def __or__(self, o):        return _Expr(lambda: self._val | _iv(o), max(self.width, _w(o)), '|', (self, o))
    def __ror__(self, o):       return _Expr(lambda: _iv(o) | self._val, max(self.width, _w(o)), '|', (o, self))
    def __xor__(self, o):       return _Expr(lambda: self._val ^ _iv(o), max(self.width, _w(o)), '^', (self, o))
    def __rxor__(self, o):      return _Expr(lambda: _iv(o) ^ self._val, max(self.width, _w(o)), '^', (o, self))
    def __lshift__(self, o):    return _Expr(lambda: (self._val << _iv(o)) & self._mask, self.width, '<<', (self, o))
    def __rlshift__(self, o):   return _Expr(lambda: _iv(o) << self._val, _w(o), '<<', (o, self))
    def __rshift__(self, o):    return _Expr(lambda: self._val >> _iv(o), self.width, '>>', (self, o))
    def __rrshift__(self, o):   return _Expr(lambda: _iv(o) >> self._val, _w(o), '>>', (o, self))
    def __invert__(self):       return _Expr(lambda: (~self._val) & self._mask, self.width, '~', (self,))
    def __neg__(self):          return _Expr(lambda: (-self._val) & self._mask, self.width, 'neg', (self,))
    def __eq__(self, o):        return _Expr(lambda: int(self._val == _iv(o)), 1, '==', (self, o))
    def __ne__(self, o):        return _Expr(lambda: int(self._val != _iv(o)), 1, '!=', (self, o))
    def __lt__(self, o):        return _Expr(lambda: int(self._val < _iv(o)), 1, '<', (self, o))
    def __le__(self, o):        return _Expr(lambda: int(self._val <= _iv(o)), 1, '<=', (self, o))
    def __gt__(self, o):        return _Expr(lambda: int(self._val > _iv(o)), 1, '>', (self, o))
    def __ge__(self, o):        return _Expr(lambda: int(self._val >= _iv(o)), 1, '>=', (self, o))
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
    def width(self):    return self._bits

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
        return o._val if isinstance(o, (Signal, _SliceProxy, _Expr)) else int(o)

    # --- arithmetic / bitwise (return _Expr for lazy evaluation) ---
    def __add__(self, o):       return _Expr(lambda: self._val + _iv(o), self._bits, '+', (self, o))
    def __radd__(self, o):      return _Expr(lambda: _iv(o) + self._val, self._bits, '+', (o, self))
    def __sub__(self, o):       return _Expr(lambda: self._val - _iv(o), self._bits, '-', (self, o))
    def __rsub__(self, o):      return _Expr(lambda: _iv(o) - self._val, self._bits, '-', (o, self))
    def __and__(self, o):       return _Expr(lambda: self._val & _iv(o), max(self._bits, _w(o)), '&', (self, o))
    def __rand__(self, o):      return _Expr(lambda: _iv(o) & self._val, max(self._bits, _w(o)), '&', (o, self))
    def __or__(self, o):        return _Expr(lambda: self._val | _iv(o), max(self._bits, _w(o)), '|', (self, o))
    def __ror__(self, o):       return _Expr(lambda: _iv(o) | self._val, max(self._bits, _w(o)), '|', (o, self))
    def __xor__(self, o):       return _Expr(lambda: self._val ^ _iv(o), max(self._bits, _w(o)), '^', (self, o))
    def __rxor__(self, o):      return _Expr(lambda: _iv(o) ^ self._val, max(self._bits, _w(o)), '^', (o, self))
    def __lshift__(self, o):    return _Expr(lambda: self._val << _iv(o), self._bits, '<<', (self, o))
    def __rlshift__(self, o):   return _Expr(lambda: _iv(o) << self._val, _w(o), '<<', (o, self))
    def __rshift__(self, o):    return _Expr(lambda: self._val >> _iv(o), self._bits, '>>', (self, o))
    def __rrshift__(self, o):   return _Expr(lambda: _iv(o) >> self._val, _w(o), '>>', (o, self))
    def __invert__(self):       return _Expr(lambda: ~self._val & self._mask, self._bits, '~', (self,))
    def __neg__(self):          return _Expr(lambda: (-self._val) & self._mask, self._bits, 'neg', (self,))
    def __eq__(self, o):        return _Expr(lambda: int(self._val == _iv(o)), 1, '==', (self, o))
    def __ne__(self, o):        return _Expr(lambda: int(self._val != _iv(o)), 1, '!=', (self, o))
    def __lt__(self, o):        return _Expr(lambda: int(self._val < _iv(o)), 1, '<', (self, o))
    def __le__(self, o):        return _Expr(lambda: int(self._val <= _iv(o)), 1, '<=', (self, o))
    def __gt__(self, o):        return _Expr(lambda: int(self._val > _iv(o)), 1, '>', (self, o))
    def __ge__(self, o):        return _Expr(lambda: int(self._val >= _iv(o)), 1, '>=', (self, o))

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

def OutputReg(width=1, reset=0):
    """Output port declared as 'output reg' — driven from @always blocks."""
    return Signal(width, reset=reset, _kind='output_reg')


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

    VALID_STYLES = (None, 'block', 'distributed', 'ultra')

    def __init__(self, depth, width=1, style=None):
        if style not in self.VALID_STYLES:
            raise ValueError(f"Mem style must be one of {self.VALID_STYLES}, got {style!r}")
        from .parameter import is_param
        self._width_param = width if is_param(width) else None
        self._depth_param = depth if is_param(depth) else None
        if self._width_param is not None:
            width = width.default
        if self._depth_param is not None:
            depth = depth.default
        self.depth = depth
        self.width = width
        self.style = style
        self.name = ''
        self._mask = (1 << width) - 1
        self._data = [0] * depth
        self._pending_write = None  # (addr, value)

    def _clone(self, param_values):
        """Create a resolved copy with concrete dimensions."""
        from .parameter import is_param
        width = self._width_param.resolve(param_values) if self._width_param else self.width
        depth = self._depth_param.resolve(param_values) if self._depth_param else self.depth
        m = Mem(depth, width, style=self.style)
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


def _sig_val(sig):
    """Read the current integer value of a Signal (or plain int)."""
    return sig._val if isinstance(sig, Signal) else int(sig)


class DualPortMem:
    """Simple dual-port memory: 1 write port + 1 read port (synchronous read).

    Generates the correct Verilog coding pattern for synthesis tool inference:
        always @(posedge clock) begin
            if (we) mem[waddr] <= wdata;
            rdata <= mem[raddr];
        end
    """

    VALID_STYLES = (None, 'block', 'distributed', 'ultra')

    def __init__(self, depth, width=1, style=None, *,
                 clock=None, we=None, waddr=None, wdata=None,
                 raddr=None, rdata=None):
        if style not in self.VALID_STYLES:
            raise ValueError(f"Mem style must be one of {self.VALID_STYLES}, got {style!r}")
        self.depth = depth
        self.width = width
        self.style = style
        self.name = ''
        self._mask = (1 << width) - 1
        self._data = [0] * depth
        self._pending_write = None
        # Port signal references
        self._clock = clock
        self._we = we
        self._waddr = waddr
        self._wdata = wdata
        self._raddr = raddr
        self._rdata = rdata

    def _register(self, module):
        """Register an always block on the parent module for simulation."""
        mem = self
        def _mem_block():
            if mem._we and _sig_val(mem._we):
                a = _sig_val(mem._waddr) % mem.depth
                d = _sig_val(mem._wdata) & mem._mask
                mem._pending_write = (a, d)
            if mem._raddr is not None and mem._rdata is not None:
                a = _sig_val(mem._raddr) % mem.depth
                mem._rdata._next = mem._data[a]
        _mem_block._veripy_mem_block = True
        edges = [Edge('posedge', mem._clock)]
        module._always_blocks.append((edges, _mem_block))

    def _tick(self):
        if self._pending_write is not None:
            addr, val = self._pending_write
            self._data[addr] = val
            self._pending_write = None


class TrueDualPortMem:
    """True dual-port memory: 2 read/write ports (synchronous reads).

    Generates the correct Verilog coding pattern for synthesis tool inference:
        always @(posedge clka) begin
            if (wea) mem[addra] <= dina;
            douta <= mem[addra];
        end
        always @(posedge clkb) begin
            if (web) mem[addrb] <= dinb;
            doutb <= mem[addrb];
        end
    """

    VALID_STYLES = (None, 'block', 'distributed', 'ultra')

    def __init__(self, depth, width=1, style=None, *,
                 clka=None, wea=None, addra=None, dina=None, douta=None,
                 clkb=None, web=None, addrb=None, dinb=None, doutb=None):
        if style not in self.VALID_STYLES:
            raise ValueError(f"Mem style must be one of {self.VALID_STYLES}, got {style!r}")
        self.depth = depth
        self.width = width
        self.style = style
        self.name = ''
        self._mask = (1 << width) - 1
        self._data = [0] * depth
        self._pending_a = None
        self._pending_b = None
        # Port A
        self._clka = clka
        self._wea = wea
        self._addra = addra
        self._dina = dina
        self._douta = douta
        # Port B
        self._clkb = clkb
        self._web = web
        self._addrb = addrb
        self._dinb = dinb
        self._doutb = doutb

    def _register(self, module):
        """Register always blocks on the parent module for simulation."""
        mem = self

        def _port_a():
            if mem._wea and _sig_val(mem._wea):
                a = _sig_val(mem._addra) % mem.depth
                d = _sig_val(mem._dina) & mem._mask
                mem._pending_a = (a, d)
            if mem._addra is not None and mem._douta is not None:
                a = _sig_val(mem._addra) % mem.depth
                mem._douta._next = mem._data[a]
        _port_a._veripy_mem_block = True
        module._always_blocks.append(([Edge('posedge', mem._clka)], _port_a))

        def _port_b():
            if mem._web and _sig_val(mem._web):
                a = _sig_val(mem._addrb) % mem.depth
                d = _sig_val(mem._dinb) & mem._mask
                mem._pending_b = (a, d)
            if mem._addrb is not None and mem._doutb is not None:
                a = _sig_val(mem._addrb) % mem.depth
                mem._doutb._next = mem._data[a]
        _port_b._veripy_mem_block = True
        module._always_blocks.append(([Edge('posedge', mem._clkb)], _port_b))

    def _tick(self):
        if self._pending_a is not None:
            addr, val = self._pending_a
            self._data[addr] = val
            self._pending_a = None
        if self._pending_b is not None:
            addr, val = self._pending_b
            self._data[addr] = val
            self._pending_b = None


# ── Built-in bit-manipulation helpers ────────────────────────────────

def clz(sig):
    """Count leading zeros. Works in behavioral sim and @comb blocks."""
    w = getattr(sig, '_width', None) or getattr(sig, 'width', 1)
    def _fn():
        v = int(sig)
        if v == 0:
            return w
        for i in range(w - 1, -1, -1):
            if (v >> i) & 1:
                return w - 1 - i
        return w
    return _Expr(_fn, w.bit_length() if isinstance(w, int) else 7, 'clz', (sig,))


def ctz(sig):
    """Count trailing zeros. Works in behavioral sim and @comb blocks."""
    w = getattr(sig, '_width', None) or getattr(sig, 'width', 1)
    def _fn():
        v = int(sig)
        if v == 0:
            return w
        for i in range(w):
            if (v >> i) & 1:
                return i
        return w
    return _Expr(_fn, w.bit_length() if isinstance(w, int) else 7, 'ctz', (sig,))


def popcount(sig):
    """Population count (number of set bits). Works in behavioral sim and @comb blocks."""
    w = getattr(sig, '_width', None) or getattr(sig, 'width', 1)
    def _fn():
        return bin(int(sig) & ((1 << w) - 1)).count('1')
    return _Expr(_fn, w.bit_length() if isinstance(w, int) else 7, 'popcount', (sig,))


def sext(sig, target_width):
    """Sign-extend a signal to target_width. Works in behavioral sim and @comb blocks."""
    src_w = getattr(sig, '_width', None) or getattr(sig, 'width', 1)
    def _fn():
        v = int(sig) & ((1 << src_w) - 1)
        if (v >> (src_w - 1)) & 1:
            v |= ((1 << target_width) - 1) ^ ((1 << src_w) - 1)
        return v & ((1 << target_width) - 1)
    return _Expr(_fn, target_width, 'sext', (sig,))
