"""Parameterized IP building blocks: reusable, synthesizable, dual-path tested."""

from .module import Module
from .signal import Input, Output, Register, Mem


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
