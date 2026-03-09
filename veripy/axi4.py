"""AXI4 full subordinate IP.

Provides :class:`Axi4Bus` (full AXI4 interface bundle with burst signals) and
:class:`Axi4Sub` (parameterized subordinate with INCR burst support and Mem
backing).

Usage::

    from veripy.axi4 import Axi4Bus, Axi4Sub

    sub = Axi4Sub(depth=256, data_width=32, addr_width=32, id_width=4)
    print(sub.to_verilog())
"""

from .signal import Interface, Input, Output, Register, Mem
from .module import Module

# AXI4 burst types
BURST_FIXED = 0b00
BURST_INCR  = 0b01
BURST_WRAP  = 0b10

# AXI4 response codes
RESP_OKAY   = 0b00
RESP_EXOKAY = 0b01
RESP_SLVERR = 0b10
RESP_DECERR = 0b11


class Axi4Bus(Interface):
    """Full AXI4 signal bundle (subordinate perspective).

    All directions are from the subordinate's point of view.
    Includes burst signals (AWLEN/ARLEN, AWSIZE/ARSIZE, AWBURST/ARBURST),
    transaction IDs, and WLAST/RLAST.
    """

    def __init__(self, data_width=32, addr_width=32, id_width=4):
        strb_width = data_width // 8
        # Write address channel
        self.awid    = Input(id_width)
        self.awaddr  = Input(addr_width)
        self.awlen   = Input(8)
        self.awsize  = Input(3)
        self.awburst = Input(2)
        self.awlock  = Input()
        self.awcache = Input(4)
        self.awprot  = Input(3)
        self.awqos   = Input(4)
        self.awvalid = Input()
        self.awready = Output()
        # Write data channel
        self.wdata   = Input(data_width)
        self.wstrb   = Input(strb_width)
        self.wlast   = Input()
        self.wvalid  = Input()
        self.wready  = Output()
        # Write response channel
        self.bid     = Output(id_width)
        self.bresp   = Output(2)
        self.bvalid  = Output()
        self.bready  = Input()
        # Read address channel
        self.arid    = Input(id_width)
        self.araddr  = Input(addr_width)
        self.arlen   = Input(8)
        self.arsize  = Input(3)
        self.arburst = Input(2)
        self.arlock  = Input()
        self.arcache = Input(4)
        self.arprot  = Input(3)
        self.arqos   = Input(4)
        self.arvalid = Input()
        self.arready = Output()
        # Read data channel
        self.rid     = Output(id_width)
        self.rdata   = Output(data_width)
        self.rresp   = Output(2)
        self.rlast   = Output()
        self.rvalid  = Output()
        self.rready  = Input()
        super().__init__()


class Axi4Sub(Module):
    """AXI4 full subordinate with INCR burst support.

    Implements independent write and read channel state machines.
    Supports one outstanding transaction per channel.
    Backed by an internal :class:`Mem` of *depth* words.

    Parameters
    ----------
    depth      : int  — memory depth in words (must be power of 2)
    data_width : int  — data bus width in bits (default 32)
    addr_width : int  — address bus width in bits (default 32)
    id_width   : int  — transaction ID width in bits (default 4)
    """

    def __init__(self, depth=256, data_width=32, addr_width=32, id_width=4):
        self.clock = Input()
        self.reset = Input()
        self.bus   = Axi4Bus(data_width, addr_width, id_width)

        strb_width = data_width // 8
        self._strb_width = strb_width
        self._shift      = strb_width.bit_length() - 1
        self._mask       = depth - 1

        self.mem = Mem(depth, data_width)

        self.wstate  = Register(2)
        self.waddr_r = Register(addr_width)
        self.wid_r   = Register(id_width)
        self.wlen_r  = Register(8)
        self.wbeat   = Register(9)

        self.rstate  = Register(2)
        self.raddr_r = Register(addr_width)
        self.rid_r   = Register(id_width)
        self.rlen_r  = Register(8)
        self.rbeat   = Register(9)

        super().__init__()

    def rtl(self):
        W_IDLE  = 0
        W_WDATA = 1
        W_WRESP = 2
        R_IDLE  = 0
        R_RDATA = 1
        shift      = self._shift
        mask       = self._mask
        strb_width = self._strb_width

        @self.posedge(self.clock)
        def write_fsm():
            if self.reset:
                self.wstate = W_IDLE
                self.wbeat  = 0
            elif self.wstate == W_IDLE:
                if self.bus.awvalid:
                    self.waddr_r = self.bus.awaddr
                    self.wid_r   = self.bus.awid
                    self.wlen_r  = self.bus.awlen
                    self.wbeat   = 0
                    self.wstate  = W_WDATA
            elif self.wstate == W_WDATA:
                if self.bus.wvalid:
                    self.mem.write((self.waddr_r >> shift) & mask, self.bus.wdata)
                    self.waddr_r = self.waddr_r + strb_width
                    self.wbeat   = self.wbeat + 1
                    if self.bus.wlast:
                        self.wstate = W_WRESP
            elif self.wstate == W_WRESP:
                if self.bus.bready:
                    self.wstate = W_IDLE

        @self.posedge(self.clock)
        def read_fsm():
            if self.reset:
                self.rstate = R_IDLE
                self.rbeat  = 0
            elif self.rstate == R_IDLE:
                if self.bus.arvalid:
                    self.raddr_r = self.bus.araddr
                    self.rid_r   = self.bus.arid
                    self.rlen_r  = self.bus.arlen
                    self.rbeat   = 0
                    self.rstate  = R_RDATA
            elif self.rstate == R_RDATA:
                if self.bus.rready:
                    self.raddr_r = self.raddr_r + strb_width
                    self.rbeat   = self.rbeat + 1
                    if self.rbeat == self.rlen_r:
                        self.rstate = R_IDLE

        @self.comb
        def outputs():
            self.bus.awready = 1 if self.wstate == W_IDLE else 0
            self.bus.wready  = 1 if self.wstate == W_WDATA else 0
            self.bus.bid     = self.wid_r
            self.bus.bresp   = 0
            self.bus.bvalid  = 1 if self.wstate == W_WRESP else 0
            self.bus.arready = 1 if self.rstate == R_IDLE else 0
            self.bus.rid     = self.rid_r
            self.bus.rdata   = self.mem[(self.raddr_r >> shift) & mask]
            self.bus.rresp   = 0
            self.bus.rlast   = 1 if self.rbeat == self.rlen_r else 0
            self.bus.rvalid  = 1 if self.rstate == R_RDATA else 0


class Axi4Crossbar:
    """AXI4 1-master to N-subordinate address-decode crossbar.

    Routes a single master's transactions to one of N subordinates based on
    address ranges.  Write and read channels are decoded independently.
    Responses are muxed back to the master.

    Parameters
    ----------
    addr_map   : list of (base, size) tuples — one entry per subordinate.
                 Addresses in ``[base, base+size)`` route to that subordinate.
    data_width : int — data bus width (default 32).
    addr_width : int — address bus width (default 32).
    id_width   : int — transaction ID width (default 4).

    After construction the module exposes:

    * ``self.m_bus``    — master-side :class:`Axi4Bus` (connect to CPU)
    * ``self.s_bus_0``  — subordinate 0 :class:`Axi4Bus`
    * ``self.s_bus_1``  — subordinate 1 :class:`Axi4Bus`
    * … one per entry in *addr_map*

    Usage::

        xbar = Axi4Crossbar(
            addr_map=[(0x0000_0000, 0x1000), (0x0001_0000, 0x1000)],
        )
    """

    def __new__(cls, addr_map, data_width=32, addr_width=32, id_width=4):
        return _build_crossbar(addr_map, data_width, addr_width, id_width)


def _build_crossbar(addr_map, data_width, addr_width, id_width):
    from .module import Module
    import types

    n = len(addr_map)

    # ── combinational routing function ───────────────────────────────
    # Write decode + route
    wr_lines = ['def _wr_route(self):']
    # Default: no subordinate selected
    for i in range(n):
        wr_lines.append(f'    self.s_bus_{i}.awid    = 0')
        wr_lines.append(f'    self.s_bus_{i}.awaddr  = 0')
        wr_lines.append(f'    self.s_bus_{i}.awlen   = 0')
        wr_lines.append(f'    self.s_bus_{i}.awsize  = 0')
        wr_lines.append(f'    self.s_bus_{i}.awburst = 0')
        wr_lines.append(f'    self.s_bus_{i}.awlock  = 0')
        wr_lines.append(f'    self.s_bus_{i}.awcache = 0')
        wr_lines.append(f'    self.s_bus_{i}.awprot  = 0')
        wr_lines.append(f'    self.s_bus_{i}.awqos   = 0')
        wr_lines.append(f'    self.s_bus_{i}.awvalid = 0')
        wr_lines.append(f'    self.s_bus_{i}.wdata   = 0')
        wr_lines.append(f'    self.s_bus_{i}.wstrb   = 0')
        wr_lines.append(f'    self.s_bus_{i}.wlast   = 0')
        wr_lines.append(f'    self.s_bus_{i}.wvalid  = 0')
        wr_lines.append(f'    self.s_bus_{i}.bready  = 0')
    wr_lines.append('    self.m_bus.awready = 0')
    wr_lines.append('    self.m_bus.wready  = 0')
    wr_lines.append('    self.m_bus.bid     = 0')
    wr_lines.append('    self.m_bus.bresp   = 3')
    wr_lines.append('    self.m_bus.bvalid  = 0')
    for i, (base, size) in enumerate(addr_map):
        kw = 'if' if i == 0 else 'elif'
        wr_lines.append(
            f'    {kw} self.m_bus.awaddr >= {base} and self.m_bus.awaddr < {base + size}:'
        )
        wr_lines.append(f'        self.s_bus_{i}.awid    = self.m_bus.awid')
        wr_lines.append(f'        self.s_bus_{i}.awaddr  = self.m_bus.awaddr')
        wr_lines.append(f'        self.s_bus_{i}.awlen   = self.m_bus.awlen')
        wr_lines.append(f'        self.s_bus_{i}.awsize  = self.m_bus.awsize')
        wr_lines.append(f'        self.s_bus_{i}.awburst = self.m_bus.awburst')
        wr_lines.append(f'        self.s_bus_{i}.awlock  = self.m_bus.awlock')
        wr_lines.append(f'        self.s_bus_{i}.awcache = self.m_bus.awcache')
        wr_lines.append(f'        self.s_bus_{i}.awprot  = self.m_bus.awprot')
        wr_lines.append(f'        self.s_bus_{i}.awqos   = self.m_bus.awqos')
        wr_lines.append(f'        self.s_bus_{i}.awvalid = self.m_bus.awvalid')
        wr_lines.append(f'        self.s_bus_{i}.wdata   = self.m_bus.wdata')
        wr_lines.append(f'        self.s_bus_{i}.wstrb   = self.m_bus.wstrb')
        wr_lines.append(f'        self.s_bus_{i}.wlast   = self.m_bus.wlast')
        wr_lines.append(f'        self.s_bus_{i}.wvalid  = self.m_bus.wvalid')
        wr_lines.append(f'        self.s_bus_{i}.bready  = self.m_bus.bready')
        wr_lines.append(f'        self.m_bus.awready = self.s_bus_{i}.awready')
        wr_lines.append(f'        self.m_bus.wready  = self.s_bus_{i}.wready')
        wr_lines.append(f'        self.m_bus.bid     = self.s_bus_{i}.bid')
        wr_lines.append(f'        self.m_bus.bresp   = self.s_bus_{i}.bresp')
        wr_lines.append(f'        self.m_bus.bvalid  = self.s_bus_{i}.bvalid')

    # Read decode + route
    rd_lines = ['def _rd_route(self):']
    for i in range(n):
        rd_lines.append(f'    self.s_bus_{i}.arid    = 0')
        rd_lines.append(f'    self.s_bus_{i}.araddr  = 0')
        rd_lines.append(f'    self.s_bus_{i}.arlen   = 0')
        rd_lines.append(f'    self.s_bus_{i}.arsize  = 0')
        rd_lines.append(f'    self.s_bus_{i}.arburst = 0')
        rd_lines.append(f'    self.s_bus_{i}.arlock  = 0')
        rd_lines.append(f'    self.s_bus_{i}.arcache = 0')
        rd_lines.append(f'    self.s_bus_{i}.arprot  = 0')
        rd_lines.append(f'    self.s_bus_{i}.arqos   = 0')
        rd_lines.append(f'    self.s_bus_{i}.arvalid = 0')
        rd_lines.append(f'    self.s_bus_{i}.rready  = 0')
    rd_lines.append('    self.m_bus.arready = 0')
    rd_lines.append('    self.m_bus.rid     = 0')
    rd_lines.append('    self.m_bus.rdata   = 0')
    rd_lines.append('    self.m_bus.rresp   = 3')
    rd_lines.append('    self.m_bus.rlast   = 0')
    rd_lines.append('    self.m_bus.rvalid  = 0')
    for i, (base, size) in enumerate(addr_map):
        kw = 'if' if i == 0 else 'elif'
        rd_lines.append(
            f'    {kw} self.m_bus.araddr >= {base} and self.m_bus.araddr < {base + size}:'
        )
        rd_lines.append(f'        self.s_bus_{i}.arid    = self.m_bus.arid')
        rd_lines.append(f'        self.s_bus_{i}.araddr  = self.m_bus.araddr')
        rd_lines.append(f'        self.s_bus_{i}.arlen   = self.m_bus.arlen')
        rd_lines.append(f'        self.s_bus_{i}.arsize  = self.m_bus.arsize')
        rd_lines.append(f'        self.s_bus_{i}.arburst = self.m_bus.arburst')
        rd_lines.append(f'        self.s_bus_{i}.arlock  = self.m_bus.arlock')
        rd_lines.append(f'        self.s_bus_{i}.arcache = self.m_bus.arcache')
        rd_lines.append(f'        self.s_bus_{i}.arprot  = self.m_bus.arprot')
        rd_lines.append(f'        self.s_bus_{i}.arqos   = self.m_bus.arqos')
        rd_lines.append(f'        self.s_bus_{i}.arvalid = self.m_bus.arvalid')
        rd_lines.append(f'        self.s_bus_{i}.rready  = self.m_bus.rready')
        rd_lines.append(f'        self.m_bus.arready = self.s_bus_{i}.arready')
        rd_lines.append(f'        self.m_bus.rid     = self.s_bus_{i}.rid')
        rd_lines.append(f'        self.m_bus.rdata   = self.s_bus_{i}.rdata')
        rd_lines.append(f'        self.m_bus.rresp   = self.s_bus_{i}.rresp')
        rd_lines.append(f'        self.m_bus.rlast   = self.s_bus_{i}.rlast')
        rd_lines.append(f'        self.m_bus.rvalid  = self.s_bus_{i}.rvalid')

    def _compile(name, lines):
        src = '\n'.join(lines)
        ns = {}
        exec(compile(src, f'<axi4crossbar:{name}>', 'exec'), ns)
        fn = ns[name]
        fn._veripy_emit_source = src
        return fn

    wr_fn = _compile('_wr_route', wr_lines)
    rd_fn = _compile('_rd_route', rd_lines)

    class _Axi4Crossbar(Module):
        def __init__(self):
            self.m_bus = Axi4Bus(data_width, addr_width, id_width)
            for i in range(n):
                object.__setattr__(self, f's_bus_{i}',
                                   Axi4Bus(data_width, addr_width, id_width))
            super().__init__()
            self._comb_blocks.append(types.MethodType(wr_fn, self))
            self._comb_blocks.append(types.MethodType(rd_fn, self))

    _Axi4Crossbar.__name__ = 'Axi4Crossbar'
    _Axi4Crossbar.__qualname__ = 'Axi4Crossbar'
    return _Axi4Crossbar()
