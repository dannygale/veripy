"""AXI4-Lite subordinate IP.

Provides :class:`Axi4LiteBus` (reusable interface bundle) and
:class:`Axi4LiteSub` (parameterized subordinate that decodes a register
map, applies byte enables, and generates AXI4-Lite response signaling).

Usage::

    from veripy.axi4lite import Axi4LiteBus, Axi4LiteSub

    sub = Axi4LiteSub(reg_map=[
        (0x00, 'ctrl',   32, 'rw'),
        (0x04, 'status', 32, 'ro'),
        (0x08, 'data',   32, 'rw'),
    ])
    print(sub.to_verilog())
"""

from .signal import Interface, Input, Output, Signal, Register


class Axi4LiteBus(Interface):
    """AXI4-Lite signal bundle (subordinate perspective).

    All directions are from the subordinate's point of view:
    inputs come from the manager, outputs go to the manager.
    """

    def __init__(self, data_width=32, addr_width=32):
        # Write address channel
        self.awaddr  = Input(addr_width)
        self.awvalid = Input()
        self.awready = Output()
        self.awprot  = Input(3)
        # Write data channel
        self.wdata   = Input(data_width)
        self.wstrb   = Input(data_width // 8)
        self.wvalid  = Input()
        self.wready  = Output()
        # Write response channel
        self.bresp   = Output(2)
        self.bvalid  = Output()
        self.bready  = Input()
        # Read address channel
        self.araddr  = Input(addr_width)
        self.arvalid = Input()
        self.arready = Output()
        self.arprot  = Input(3)
        # Read data channel
        self.rdata   = Output(data_width)
        self.rresp   = Output(2)
        self.rvalid  = Output()
        self.rready  = Input()
        super().__init__()


# AXI4-Lite response codes
RESP_OKAY   = 0b00
RESP_DECERR = 0b11


class Axi4LiteSub:
    """AXI4-Lite subordinate with register map decode.

    Parameters
    ----------
    reg_map : list of (offset, name, width, access)
        Register definitions.  *offset* is the byte address, *name* becomes
        an attribute on the module, *width* is in bits, *access* is one of
        ``'rw'``, ``'ro'``, ``'wo'``.
    data_width : int
        Bus data width (default 32).
    addr_width : int
        Bus address width (default 32).

    After construction the module exposes:

    * ``self.bus`` — :class:`Axi4LiteBus` interface
    * ``self.<name>`` — :class:`Register` for each register map entry
    """

    def __new__(cls, *args, **kwargs):
        reg_map    = kwargs.get('reg_map',    args[0] if args else [])
        data_width = kwargs.get('data_width', args[1] if len(args) > 1 else 32)
        addr_width = kwargs.get('addr_width', args[2] if len(args) > 2 else 32)
        return _build_sub(reg_map, data_width, addr_width)


def _gen_source(name, lines):
    """Join *lines* into a function source and compile it.

    Returns ``(func, source_str)`` where *func* is the compiled function
    and *source_str* is attached as ``_veripy_emit_source`` so the Verilog
    lowerer can parse it.
    """
    src = '\n'.join(lines)
    ns = {}
    exec(compile(src, f'<axi4lite:{name}>', 'exec'), ns)
    fn = ns[name]
    fn._veripy_emit_source = src
    return fn


def _build_sub(reg_map, data_width, addr_width):
    """Construct an Axi4LiteSub Module instance."""
    from .module import Module

    strb_width = data_width // 8
    readable = [(off, name) for off, name, _w, acc in reg_map if acc != 'wo']
    writable = [(off, name) for off, name, _w, acc in reg_map if acc != 'ro']

    # ── read_decode (comb) ───────────────────────────────────────────
    rd = [
        'def read_decode(self):',
        '    self.bus.arready = 1 if self.bus.arvalid else 0',
        '    self.bus.rvalid = 0',
        '    self.bus.rdata = 0',
        '    self.bus.rresp = 0',
        '    if self.bus.arvalid:',
    ]
    if readable:
        for i, (off, name) in enumerate(readable):
            kw = 'if' if i == 0 else 'elif'
            rd.append(f'        {kw} self.bus.araddr == {off}:')
            rd.append(f'            self.bus.rdata = self.{name}')
            rd.append(f'            self.bus.rvalid = 1')
        rd.append('        else:')
        rd.append('            self.bus.rresp = 3')
        rd.append('            self.bus.rvalid = 1')
    else:
        rd.append('        self.bus.rresp = 3')
        rd.append('        self.bus.rvalid = 1')
    read_fn = _gen_source('read_decode', rd)

    # ── handshake (comb) ─────────────────────────────────────────────
    hs = [
        'def handshake(self):',
        '    self.bus.awready = 1 if self.bus.awvalid else 0',
        '    self.bus.wready = 1 if self.bus.wvalid else 0',
        '    self.bus.bvalid = 1 if self.bus.awvalid and self.bus.wvalid else 0',
        '    self.bus.bresp = 0',
    ]
    hs_fn = _gen_source('handshake', hs)

    # ── write_logic (posedge clock) ──────────────────────────────────
    wr = [
        'def write_logic(self):',
        '    if self.reset:',
    ]
    has_writable_reset = False
    for _off, name, _w, acc in reg_map:
        if acc != 'ro':
            wr.append(f'        self.{name} = 0')
            has_writable_reset = True
    if not has_writable_reset:
        wr.append('        pass')
    wr.append('    elif self.bus.awvalid and self.bus.wvalid:')
    if writable:
        for i, (off, name) in enumerate(writable):
            kw = 'if' if i == 0 else 'elif'
            wr.append(f'        {kw} self.bus.awaddr == {off}:')
            # Byte-enable masking
            wr.append(f'            _mask = 0')
            for b in range(strb_width):
                wr.append(f'            if self.bus.wstrb & {1 << b}:')
                wr.append(f'                _mask = _mask | {0xFF << (b * 8)}')
            wr.append(f'            self.{name} = (self.{name} & ~_mask) | (self.bus.wdata & _mask)')
    else:
        wr.append('        pass')
    write_fn = _gen_source('write_logic', wr)

    # ── Module class ─────────────────────────────────────────────────
    class _Axi4LiteSub(Module):
        def __init__(self):
            self.clock = Input()
            self.reset = Input()
            self.bus   = Axi4LiteBus(data_width, addr_width)

            self._reg_map = reg_map
            for _off, name, width, _acc in reg_map:
                object.__setattr__(self, name, Register(width))

            self._read_fn  = read_fn
            self._hs_fn    = hs_fn
            self._write_fn = write_fn
            super().__init__()

        def rtl(self):
            import types
            self._comb_blocks.append(types.MethodType(self._read_fn, self))
            self._comb_blocks.append(types.MethodType(self._hs_fn, self))
            from .signal import posedge as _posedge
            self._always_blocks.append(([_posedge(self.clock)],
                                        types.MethodType(self._write_fn, self)))

    _Axi4LiteSub.__name__ = 'Axi4LiteSub'
    _Axi4LiteSub.__qualname__ = 'Axi4LiteSub'
    return _Axi4LiteSub()
