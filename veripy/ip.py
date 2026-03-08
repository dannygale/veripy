"""Parameterized IP building blocks: reusable, synthesizable, dual-path tested."""

from .module import Module
from .signal import Input, Output, Register, Mem
from .blackbox import BlackBox


class SyncFifo(Module):
    """Synchronous FIFO with configurable depth and width.

    Ports:
        clock, reset       — system signals
        push, din, full    — write side
        pop, dout, empty   — read side
        count              — current occupancy

    Usage:
        fifo = SyncFifo(width=8, depth=16)
    """

    def __init__(self, width=8, depth=4):
        self.clock = Input()
        self.reset = Input()
        self.push  = Input()
        self.din   = Input(width)
        self.full  = Output()
        self.pop   = Input()
        self.dout  = Output(width)
        self.empty = Output()
        self.count = Output(depth.bit_length() + 1 if isinstance(depth, int) and depth > 0
                            else 8)

        ptr_bits = (depth - 1).bit_length() + 1
        cnt_bits = depth.bit_length() + 1
        self.mem  = Mem(depth, width)
        self.wptr = Register(ptr_bits)
        self.rptr = Register(ptr_bits)
        self.cnt  = Register(cnt_bits)
        super().__init__()

        mask = depth - 1

        @self.comb
        def flags():
            self.full  = 1 if self.cnt == depth else 0
            self.empty = 1 if self.cnt == 0 else 0
            self.dout  = self.mem[self.rptr & mask]
            self.count = self.cnt

        @self.posedge(self.clock)
        def update():
            if self.reset:
                self.wptr = 0
                self.rptr = 0
                self.cnt  = 0
            else:
                if self.push and self.cnt != depth:
                    self.mem.write(self.wptr & mask, self.din)
                    self.wptr = self.wptr + 1
                    self.cnt  = self.cnt + 1
                if self.pop and self.cnt != 0:
                    self.rptr = self.rptr + 1
                    self.cnt  = self.cnt - 1


class EdgeDetector(Module):
    """Rising/falling/both edge detector.

    Ports:
        clock, reset  — system signals
        d             — input signal
        rise          — high for one cycle on rising edge
        fall          — high for one cycle on falling edge
        toggle        — high for one cycle on any edge

    All outputs are registered (one cycle latency).

    Usage:
        ed = EdgeDetector()
    """

    def __init__(self):
        self.clock  = Input()
        self.reset  = Input()
        self.d      = Input()
        self.rise   = Output()
        self.fall   = Output()
        self.toggle = Output()
        self.prev   = Register()
        self.rise_r = Register()
        self.fall_r = Register()
        self.tog_r  = Register()
        super().__init__()

        @self.posedge(self.clock)
        def sample():
            if self.reset:
                self.prev   = 0
                self.rise_r = 0
                self.fall_r = 0
                self.tog_r  = 0
            else:
                self.rise_r = self.d and not self.prev
                self.fall_r = not self.d and self.prev
                self.tog_r  = self.d ^ self.prev
                self.prev   = self.d

        @self.comb
        def output():
            self.rise   = self.rise_r
            self.fall   = self.fall_r
            self.toggle = self.tog_r


class Debouncer(Module):
    """Button/signal debouncer with configurable threshold.

    Holds output stable until input is steady for `threshold` clock cycles.

    Ports:
        clock, reset  — system signals
        d             — noisy input
        q             — debounced output

    Usage:
        db = Debouncer(threshold=1000)
    """

    def __init__(self, threshold=1000):
        self.clock = Input()
        self.reset = Input()
        self.d     = Input()
        self.q     = Output()

        cnt_bits = (threshold).bit_length() + 1
        self.cnt = Register(cnt_bits)
        self.qr  = Register()
        super().__init__()

        @self.posedge(self.clock)
        def logic():
            if self.reset:
                self.cnt = 0
                self.qr  = 0
            elif self.d != self.qr:
                if self.cnt == threshold - 1:
                    self.qr  = self.d
                    self.cnt = 0
                else:
                    self.cnt = self.cnt + 1
            else:
                self.cnt = 0

        @self.comb
        def output():
            self.q = self.qr


class RoundRobinArbiter(Module):
    """Round-robin arbiter for N requestors.

    Grants one request per cycle in round-robin order. If no requests
    are active, grant is 0. The arbiter is fair: it rotates priority
    after each grant.

    Ports:
        clock, reset  — system signals
        req           — N-bit request vector (one-hot or multi-hot)
        grant         — N-bit grant vector (one-hot, at most one bit set)

    Usage:
        arb = RoundRobinArbiter(n=4)
    """

    def __init__(self, n=4):
        self.clock = Input()
        self.reset = Input()
        self.req   = Input(n)
        self.grant = Output(n)
        self.ptr   = Register(n.bit_length())
        self.grnt  = Register(n)
        super().__init__()

        @self.posedge(self.clock)
        def update():
            if self.reset:
                self.ptr  = 0
                self.grnt = 0
            elif self.req:
                for p in range(n):
                    if self.ptr == p:
                        found = 0
                        for i in range(n):
                            idx = (p + i) % n
                            if not found and (self.req & (1 << idx)):
                                self.ptr  = (idx + 1) % n
                                self.grnt = 1 << idx
                                found = 1
            else:
                self.grnt = 0

        @self.comb
        def output():
            self.grant = self.grnt


class PriorityArbiter(Module):
    """Fixed-priority arbiter for N requestors.

    Lowest index = highest priority. Grant is one-hot.

    Ports:
        clock, reset  — system signals
        req           — N-bit request vector
        grant         — N-bit grant vector (one-hot)

    Usage:
        arb = PriorityArbiter(n=4)
    """

    def __init__(self, n=4):
        self.clock = Input()
        self.reset = Input()
        self.req   = Input(n)
        self.grant = Output(n)
        self.grnt  = Register(n)
        super().__init__()

        @self.posedge(self.clock)
        def update():
            if self.reset:
                self.grnt = 0
            elif self.req:
                found = 0
                for i in range(n):
                    if not found and (self.req & (1 << i)):
                        self.grnt = 1 << i
                        found = 1
            else:
                self.grnt = 0

        @self.comb
        def output():
            self.grant = self.grnt


class ClockDivider(Module):
    """Parameterized clock divider.

    Output toggles every ``divisor`` input clock cycles, producing
    a clock with period = 2 * divisor * input period.

    Ports:
        clock, reset  — system signals
        clk_out       — divided clock output

    Usage:
        div = ClockDivider(divisor=4)
    """

    def __init__(self, divisor=2):
        self.clock   = Input()
        self.reset   = Input()
        self.clk_out = Output()

        cnt_bits = max(1, (divisor - 1).bit_length())
        self.cnt  = Register(cnt_bits)
        self.out_r = Register()
        super().__init__()

        @self.posedge(self.clock)
        def divide():
            if self.reset:
                self.cnt   = 0
                self.out_r = 0
            elif self.cnt == divisor - 1:
                self.cnt   = 0
                self.out_r = not self.out_r
            else:
                self.cnt = self.cnt + 1

        @self.comb
        def output():
            self.clk_out = self.out_r


class CreditFlowControl(Module):
    """Credit-based flow control.

    Sender may send when credits > 0. Each send consumes a credit;
    each recv returns a credit.

    Ports:
        clock, reset   — system signals
        send_valid     — input: sender has data
        send_ready     — output: credits available (sender may send)
        recv_valid     — input: receiver consumed data (returns credit)
        recv_ready     — output: receiver can accept (always 1 when credits < max)

    Usage:
        fc = CreditFlowControl(credits=4)
    """

    def __init__(self, credits=4):
        self.clock      = Input()
        self.reset      = Input()
        self.send_valid = Input()
        self.send_ready = Output()
        self.recv_valid = Input()
        self.recv_ready = Output()

        cnt_bits = credits.bit_length() + 1
        self.cnt = Register(cnt_bits)
        super().__init__()

        @self.posedge(self.clock)
        def update():
            if self.reset:
                self.cnt = credits
            else:
                send = self.send_valid and self.cnt != 0
                recv = self.recv_valid and self.cnt != credits
                if send and not recv:
                    self.cnt = self.cnt - 1
                elif recv and not send:
                    self.cnt = self.cnt + 1

        @self.comb
        def output():
            self.send_ready = 1 if self.cnt != 0 else 0
            self.recv_ready = 1 if self.cnt != credits else 0


class DmaEngine(Module):
    """Simple memory-to-memory DMA engine template.

    Transfers *length* words from *src_addr* to *dst_addr* using a
    generic read/write memory interface.  Assert ``start`` for one cycle
    to begin a transfer; ``done`` pulses for one cycle on completion.

    The read and write interfaces use a simple valid/ready handshake:

    * **Read**: assert ``rd_en`` with ``rd_addr``; data arrives on
      ``rd_data`` when ``rd_valid`` is high.
    * **Write**: assert ``wr_en`` with ``wr_addr`` and ``wr_data``;
      accepted when ``wr_ready`` is high.

    Ports:
        clock, reset   — system signals
        start          — pulse to begin transfer
        src_addr       — source start address
        dst_addr       — destination start address
        length         — number of words to transfer
        done           — pulses for one cycle when transfer completes
        busy           — high while transfer is in progress
        rd_addr        — read address output
        rd_en          — read enable output
        rd_data        — read data input
        rd_valid       — read data valid input
        wr_addr        — write address output
        wr_en          — write enable output
        wr_data        — write data output
        wr_ready       — write accepted input

    Usage::

        dma = DmaEngine(addr_width=32, data_width=32)
    """

    def __init__(self, addr_width=32, data_width=32):
        self.clock    = Input()
        self.reset    = Input()
        self.start    = Input()
        self.src_addr = Input(addr_width)
        self.dst_addr = Input(addr_width)
        self.length   = Input(addr_width)
        self.done     = Output()
        self.busy     = Output()

        # Read interface
        self.rd_addr  = Output(addr_width)
        self.rd_en    = Output()
        self.rd_data  = Input(data_width)
        self.rd_valid = Input()

        # Write interface
        self.wr_addr  = Output(addr_width)
        self.wr_en    = Output()
        self.wr_data  = Output(data_width)
        self.wr_ready = Input()

        strb_width = data_width // 8

        # State: 0=IDLE, 1=READ, 2=WRITE, 3=DONE
        self.state    = Register(2)
        self.cur_src  = Register(addr_width)
        self.cur_dst  = Register(addr_width)
        self.remain   = Register(addr_width)
        self.rdbuf    = Register(data_width)   # read data buffer
        super().__init__()

        S_IDLE  = 0
        S_READ  = 1
        S_WRITE = 2
        S_DONE  = 3

        @self.posedge(self.clock)
        def fsm():
            if self.reset:
                self.state   = S_IDLE
                self.remain  = 0
            elif self.state == S_IDLE:
                if self.start:
                    self.cur_src = self.src_addr
                    self.cur_dst = self.dst_addr
                    self.remain  = self.length
                    self.state   = S_READ
            elif self.state == S_READ:
                if self.rd_valid:
                    self.rdbuf  = self.rd_data
                    self.state  = S_WRITE
            elif self.state == S_WRITE:
                if self.wr_ready:
                    self.cur_src = self.cur_src + strb_width
                    self.cur_dst = self.cur_dst + strb_width
                    self.remain  = self.remain - 1
                    if self.remain == 1:
                        self.state = S_DONE
                    else:
                        self.state = S_READ
            elif self.state == S_DONE:
                self.state = S_IDLE

        @self.comb
        def outputs():
            self.rd_addr = self.cur_src
            self.rd_en   = 1 if self.state == S_READ else 0
            self.wr_addr = self.cur_dst
            self.wr_en   = 1 if self.state == S_WRITE else 0
            self.wr_data = self.rdbuf
            self.done    = 1 if self.state == S_DONE else 0
            self.busy    = 1 if self.state != S_IDLE else 0


class IntController(Module):
    """Edge-triggered interrupt controller for N interrupt sources.

    Each interrupt source has an enable bit (``ier``) and a pending bit
    (``ipr``).  A rising edge on ``irq[i]`` sets ``ipr[i]``.  Software
    clears pending bits by asserting the corresponding bit in ``ipr_clr``
    for one cycle.  ``irq_out`` is asserted whenever any enabled interrupt
    is pending.

    Ports:
        clock, reset   — system signals
        irq            — N-bit interrupt request inputs (level, edge-detected)
        irq_out        — 1-bit interrupt output to CPU
        ier            — N-bit interrupt enable register (current value)
        ier_we         — write enable for ier
        ier_wdata      — write data for ier
        ipr            — N-bit interrupt pending register (read-only)
        ipr_clr        — N-bit write-1-to-clear for ipr

    Usage::

        ic = IntController(n=8)
    """

    def __init__(self, n=8):
        self.clock     = Input()
        self.reset     = Input()
        self.irq       = Input(n)
        self.irq_out   = Output()
        self.ier       = Output(n)
        self.ier_we    = Input()
        self.ier_wdata = Input(n)
        self.ipr       = Output(n)
        self.ipr_clr   = Input(n)

        self.ier_r  = Register(n)
        self.ipr_r  = Register(n)
        self.irq_d  = Register(n)   # previous irq for edge detection
        super().__init__()

        @self.posedge(self.clock)
        def update():
            if self.reset:
                self.ier_r = 0
                self.ipr_r = 0
                self.irq_d = 0
            else:
                # Rising-edge detect: set pending bits; clear via ipr_clr
                self.ipr_r = (self.ipr_r | (self.irq & ~self.irq_d)) & ~self.ipr_clr
                self.irq_d = self.irq
                if self.ier_we:
                    self.ier_r = self.ier_wdata

        @self.comb
        def outputs():
            self.ier     = self.ier_r
            self.ipr     = self.ipr_r
            self.irq_out = 1 if (self.ipr_r & self.ier_r) else 0


# ── DDR memory controller ─────────────────────────────────────────────

class DdrPhy(BlackBox):
    """BlackBox wrapper for a vendor DDR PHY.

    Provides the standard DDR3/DDR4 physical interface signals.  The
    ``verilog_module_name`` defaults to ``ddr_phy`` — override it with
    the actual vendor primitive name (e.g. ``MIG_7SERIES``, ``ddr4``).

    Ports (PHY side — connect directly to FPGA/ASIC I/O):
        ck_p / ck_n    — differential clock
        cke            — clock enable
        cs_n           — chip select (active low)
        ras_n          — row address strobe (active low)
        cas_n          — column address strobe (active low)
        we_n           — write enable (active low)
        ba             — bank address
        addr           — row/column address
        dq             — data bus (bidirectional, modelled as in+out)
        dqs_p / dqs_n  — data strobe (differential)
        dm             — data mask
        odt            — on-die termination

    Ports (controller side):
        sys_clk        — system clock input
        sys_reset      — system reset input
        init_done      — PHY initialisation complete
        app_addr       — application address
        app_cmd        — command (0=write, 1=read)
        app_en         — command enable
        app_rdy        — command accepted
        app_wdf_data   — write data
        app_wdf_wren   — write data enable
        app_wdf_rdy    — write data accepted
        app_rd_data    — read data
        app_rd_valid   — read data valid

    Usage::

        phy = DdrPhy(data_width=16, addr_width=15, bank_width=3,
                     verilog_module_name='MIG_7SERIES')
    """

    def __init__(self, data_width=16, addr_width=15, bank_width=3,
                 verilog_module_name='ddr_phy'):
        # PHY I/O
        self.ck_p    = Output()
        self.ck_n    = Output()
        self.cke     = Output()
        self.cs_n    = Output()
        self.ras_n   = Output()
        self.cas_n   = Output()
        self.we_n    = Output()
        self.ba      = Output(bank_width)
        self.addr    = Output(addr_width)
        self.dq_in   = Input(data_width)
        self.dq_out  = Output(data_width)
        self.dqs_p   = Output(data_width // 8)
        self.dqs_n   = Output(data_width // 8)
        self.dm      = Output(data_width // 8)
        self.odt     = Output()
        # Controller interface
        self.sys_clk      = Input()
        self.sys_reset    = Input()
        self.init_done    = Output()
        self.app_addr     = Input(addr_width + bank_width + 3)
        self.app_cmd      = Input(3)
        self.app_en       = Input()
        self.app_rdy      = Output()
        self.app_wdf_data = Input(data_width * 4)
        self.app_wdf_wren = Input()
        self.app_wdf_rdy  = Output()
        self.app_rd_data  = Output(data_width * 4)
        self.app_rd_valid = Output()
        super().__init__(verilog_module_name=verilog_module_name)


class DdrController(Module):
    """Simple DDR controller wrapper around :class:`DdrPhy`.

    Provides a word-addressed read/write interface on top of the DDR PHY
    application port.  Handles the init_done gate and basic command
    sequencing.

    Ports:
        clock, reset   — system signals (forwarded to PHY as sys_clk/sys_reset)
        ready          — high when PHY init is done and controller is idle
        addr           — word address for read/write
        we             — write enable
        wdata          — write data
        re             — read enable
        rdata          — read data
        rvalid         — read data valid

    Usage::

        ctrl = DdrController(data_width=16, addr_width=15)
    """

    def __init__(self, data_width=16, addr_width=15, bank_width=3,
                 verilog_module_name='ddr_phy'):
        self.clock  = Input()
        self.reset  = Input()
        self.ready  = Output()
        self.addr   = Input(addr_width + bank_width + 3)
        self.we     = Input()
        self.wdata  = Input(data_width * 4)
        self.re     = Input()
        self.rdata  = Output(data_width * 4)
        self.rvalid = Output()

        self.phy = DdrPhy(data_width, addr_width, bank_width,
                          verilog_module_name=verilog_module_name)
        self.rvalid_r = Register()
        self.rdata_r  = Register(data_width * 4)
        super().__init__()

        @self.posedge(self.clock)
        def capture():
            if self.reset:
                self.rvalid_r = 0
                self.rdata_r  = 0
            else:
                self.rvalid_r = self.phy.app_rd_valid
                self.rdata_r  = self.phy.app_rd_data

        @self.comb
        def wire():
            self.phy.sys_clk      = self.clock
            self.phy.sys_reset    = self.reset
            self.phy.app_addr     = self.addr
            self.phy.app_cmd      = 0 if self.we else 1
            self.phy.app_en       = self.we | self.re
            self.phy.app_wdf_data = self.wdata
            self.phy.app_wdf_wren = self.we
            self.ready            = self.phy.init_done & self.phy.app_rdy
            self.rdata            = self.rdata_r
            self.rvalid           = self.rvalid_r


# ── JTAG debug transport ──────────────────────────────────────────────

class JtagTap(BlackBox):
    """BlackBox wrapper for a vendor JTAG TAP controller.

    Provides the standard JTAG interface plus a debug register bus.
    Override ``verilog_module_name`` with the actual vendor primitive.

    Ports (JTAG I/O):
        tck    — test clock
        tms    — test mode select
        tdi    — test data in
        tdo    — test data out

    Ports (debug bus):
        dbg_clk    — debug clock output (typically = tck)
        dbg_addr   — debug register address
        dbg_we     — debug write enable
        dbg_wdata  — debug write data
        dbg_rdata  — debug read data
        dbg_valid  — transaction valid

    Usage::

        tap = JtagTap(data_width=32, addr_width=7,
                      verilog_module_name='jtag_tap')
    """

    def __init__(self, data_width=32, addr_width=7,
                 verilog_module_name='jtag_tap'):
        self.tck       = Input()
        self.tms       = Input()
        self.tdi       = Input()
        self.tdo       = Output()
        self.dbg_clk   = Output()
        self.dbg_addr  = Output(addr_width)
        self.dbg_we    = Output()
        self.dbg_wdata = Output(data_width)
        self.dbg_rdata = Input(data_width)
        self.dbg_valid = Output()
        super().__init__(verilog_module_name=verilog_module_name)


class DebugModule(Module):
    """JTAG debug module wrapping :class:`JtagTap`.

    Exposes a simple register file accessible over JTAG.  The debug
    registers are readable and writable from both the JTAG side and the
    system side.

    Ports:
        tck, tms, tdi, tdo  — JTAG signals (pass-through to JtagTap)
        clock, reset        — system clock/reset
        dbg_addr            — system-side debug register address
        dbg_we              — system-side write enable
        dbg_wdata           — system-side write data
        dbg_rdata           — system-side read data

    Usage::

        dm = DebugModule(data_width=32, addr_width=7, n_regs=128)
    """

    def __init__(self, data_width=32, addr_width=7, n_regs=128,
                 verilog_module_name='jtag_tap'):
        self.tck       = Input()
        self.tms       = Input()
        self.tdi       = Input()
        self.tdo       = Output()
        self.clock     = Input()
        self.reset     = Input()
        self.dbg_addr  = Input(addr_width)
        self.dbg_we    = Input()
        self.dbg_wdata = Input(data_width)
        self.dbg_rdata = Output(data_width)

        self.tap  = JtagTap(data_width, addr_width,
                            verilog_module_name=verilog_module_name)
        self.regs = Mem(n_regs, data_width)
        self.rdata_r = Register(data_width)
        super().__init__()

        @self.posedge(self.clock)
        def sys_access():
            if self.reset:
                self.rdata_r = 0
            else:
                if self.dbg_we:
                    self.regs.write(self.dbg_addr, self.dbg_wdata)
                self.rdata_r = self.regs[self.dbg_addr]

        @self.posedge(self.tap.dbg_clk)
        def jtag_access():
            if self.tap.dbg_valid:
                if self.tap.dbg_we:
                    self.regs.write(self.tap.dbg_addr, self.tap.dbg_wdata)
                self.tap.dbg_rdata = self.regs[self.tap.dbg_addr]

        @self.comb
        def wire():
            self.tap.tck   = self.tck
            self.tap.tms   = self.tms
            self.tap.tdi   = self.tdi
            self.tdo       = self.tap.tdo
            self.dbg_rdata = self.rdata_r
