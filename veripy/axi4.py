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
        shift      = strb_width.bit_length() - 1   # byte→word address shift
        mask       = depth - 1

        self.mem = Mem(depth, data_width)

        # Write FSM registers  (0=IDLE, 1=WDATA, 2=WRESP)
        self.wstate  = Register(2)
        self.waddr_r = Register(addr_width)
        self.wid_r   = Register(id_width)
        self.wlen_r  = Register(8)
        self.wbeat   = Register(9)

        # Read FSM registers  (0=IDLE, 1=RDATA)
        self.rstate  = Register(2)
        self.raddr_r = Register(addr_width)
        self.rid_r   = Register(id_width)
        self.rlen_r  = Register(8)
        self.rbeat   = Register(9)

        super().__init__()

        W_IDLE  = 0
        W_WDATA = 1
        W_WRESP = 2
        R_IDLE  = 0
        R_RDATA = 1

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
            # Write address channel
            self.bus.awready = 1 if self.wstate == W_IDLE else 0
            # Write data channel
            self.bus.wready  = 1 if self.wstate == W_WDATA else 0
            # Write response channel
            self.bus.bid     = self.wid_r
            self.bus.bresp   = 0
            self.bus.bvalid  = 1 if self.wstate == W_WRESP else 0
            # Read address channel
            self.bus.arready = 1 if self.rstate == R_IDLE else 0
            # Read data channel
            self.bus.rid     = self.rid_r
            self.bus.rdata   = self.mem[(self.raddr_r >> shift) & mask]
            self.bus.rresp   = 0
            self.bus.rlast   = 1 if self.rbeat == self.rlen_r else 0
            self.bus.rvalid  = 1 if self.rstate == R_RDATA else 0
