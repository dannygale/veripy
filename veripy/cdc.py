"""CDC primitives: Synchronizer and AsyncFIFO."""

from .module import Module
from .signal import Signal, Input, Output, Register, Mem


class Synchronizer(Module):
    """Multi-stage flip-flop synchronizer for safe clock domain crossing.

    Usage:
        self.sync = Synchronizer(width=8, stages=2)
        # wire: self.sync.clk, self.sync.rst, self.sync.d → self.sync.q
    """
    _cdc_safe = True

    def __init__(self, width=1, stages=2):
        self.clk = Input()
        self.rst = Input()
        self.d   = Input(width)
        self.q   = Output(width)
        for i in range(stages):
            setattr(self, f'_stage{i}', Register(width))
        super().__init__()
        self._n_stages = stages

        @self.comb
        def output():
            self.q = getattr(self, f'_stage{stages - 1}')

        @self.posedge(self.clk)
        def shift():
            if self.rst:
                for i in range(stages):
                    getattr(self, f'_stage{i}')._val = 0
            else:
                # Snapshot before updating to get real pipeline latency
                vals = [int(getattr(self, f'_stage{i}')) for i in range(stages)]
                getattr(self, '_stage0')._val = int(self.d)
                for i in range(1, stages):
                    getattr(self, f'_stage{i}')._val = vals[i - 1]


def _bin2gray(b, bits):
    return b ^ (b >> 1)


def _gray2bin(g, bits):
    b = g
    shift = 1
    while shift < bits:
        b ^= (b >> shift)
        shift <<= 1
    return b


class AsyncFIFO(Module):
    """Asynchronous FIFO with gray-code pointers for safe CDC.

    Depth must be a power of 2.

    Usage:
        self.fifo = AsyncFIFO(width=8, depth=16)
        # Write side: fifo.wclk, fifo.wrst, fifo.wen, fifo.wdata, fifo.full
        # Read side:  fifo.rclk, fifo.rrst, fifo.ren, fifo.rdata, fifo.empty
    """
    _cdc_safe = True

    def __init__(self, width=8, depth=16):
        assert depth > 0 and (depth & (depth - 1)) == 0, "depth must be power of 2"
        addr_bits = depth.bit_length()  # extra bit for full/empty

        self.wclk  = Input()
        self.wrst  = Input()
        self.wen   = Input()
        self.wdata = Input(width)
        self.full  = Output()

        self.rclk  = Input()
        self.rrst  = Input()
        self.ren   = Input()
        self.rdata = Output(width)
        self.empty = Output()

        self.mem     = Mem(depth, width)
        self.wptr    = Register(addr_bits)  # binary write pointer
        self.rptr    = Register(addr_bits)  # binary read pointer
        self.wgray   = Register(addr_bits)  # gray-code write pointer
        self.rgray   = Register(addr_bits)  # gray-code read pointer
        # Synchronized gray pointers (2-stage each)
        self.wgray_s1 = Register(addr_bits)
        self.wgray_s2 = Register(addr_bits)
        self.rgray_s1 = Register(addr_bits)
        self.rgray_s2 = Register(addr_bits)

        super().__init__()
        self._depth = depth
        self._addr_bits = addr_bits
        mask = depth - 1

        @self.comb
        def flags():
            self.rdata = self.mem[int(self.rptr) & mask]
            self.empty = 1 if int(self.rgray) == int(self.wgray_s2) else 0
            # Full when gray pointers differ in top 2 bits, match in rest
            wg = int(self.wgray)
            rg_s = int(self.rgray_s2)
            top_mask = (1 << addr_bits) - 1
            full_cond = (wg ^ rg_s) == (3 << (addr_bits - 2))
            self.full = 1 if full_cond else 0

        @self.posedge(self.wclk)
        def write_logic():
            if self.wrst:
                self.wptr._val = 0
                self.wgray._val = 0
                self.rgray_s1._val = 0
                self.rgray_s2._val = 0
            else:
                # Sync read gray pointer into write domain
                self.rgray_s2._val = int(self.rgray_s1)
                self.rgray_s1._val = int(self.rgray)
                if self.wen and not int(self.full):
                    wp = int(self.wptr)
                    self.mem.write(wp & mask, int(self.wdata))
                    new_wp = (wp + 1) % (1 << addr_bits)
                    self.wptr._val = new_wp
                    self.wgray._val = _bin2gray(new_wp, addr_bits)

        @self.posedge(self.rclk)
        def read_logic():
            if self.rrst:
                self.rptr._val = 0
                self.rgray._val = 0
                self.wgray_s1._val = 0
                self.wgray_s2._val = 0
            else:
                # Sync write gray pointer into read domain
                self.wgray_s2._val = int(self.wgray_s1)
                self.wgray_s1._val = int(self.wgray)
                if self.ren and not int(self.empty):
                    rp = int(self.rptr)
                    new_rp = (rp + 1) % (1 << addr_bits)
                    self.rptr._val = new_rp
                    self.rgray._val = _bin2gray(new_rp, addr_bits)
