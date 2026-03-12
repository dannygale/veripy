"""CDC primitives: Synchronizer and AsyncFIFO."""

from .module import Module
from .signal import Signal, Input, Output, Register, Mem


class Synchronizer(Module):
    """Multi-stage flip-flop synchronizer for safe clock domain crossing."""
    _cdc_safe = True

    def __init__(self, width=1, stages=2):
        self.clk = Input()
        self.rst = Input()
        self.d   = Input(width)
        self.q   = Output(width)
        for i in range(stages):
            setattr(self, f'_stage{i}', Register(width))
        self._n_stages = stages
        super().__init__()

    def rtl(self):
        stages = self._n_stages

        @self.comb
        def output():
            self.q = getattr(self, f'_stage{stages - 1}')

        output._veripy_emit_source = (
            f'def output():\n'
            f'    self.q = self._stage{stages - 1}\n'
        )

        @self.posedge(self.clk)
        def shift():
            if self.rst:
                for i in range(stages):
                    getattr(self, f'_stage{i}')._val = 0
            else:
                vals = [int(getattr(self, f'_stage{i}')) for i in range(stages)]
                getattr(self, '_stage0')._val = int(self.d)
                for i in range(1, stages):
                    getattr(self, f'_stage{i}')._val = vals[i - 1]

        reset_lines = '\n'.join(
            f'        self._stage{i} = 0' for i in range(stages))
        shift_lines = '\n'.join(
            f'        self._stage{i} = self._stage{i - 1}'
            for i in range(stages - 1, 0, -1)
        ) + f'\n        self._stage0 = self.d'

        shift._veripy_emit_source = (
            f'def shift():\n'
            f'    if self.rst:\n'
            f'{reset_lines}\n'
            f'    else:\n'
            f'{shift_lines}\n'
        )


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
    """Asynchronous FIFO with gray-code pointers for safe CDC."""
    _cdc_safe = True

    def __init__(self, width=8, depth=16):
        assert depth > 0 and (depth & (depth - 1)) == 0, "depth must be power of 2"
        addr_bits = depth.bit_length()

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

        self.mem      = Mem(depth, width)
        self.wptr     = Register(addr_bits)
        self.rptr     = Register(addr_bits)
        self.wgray    = Register(addr_bits)
        self.rgray    = Register(addr_bits)
        self.wgray_s1 = Register(addr_bits)
        self.wgray_s2 = Register(addr_bits)
        self.rgray_s1 = Register(addr_bits)
        self.rgray_s2 = Register(addr_bits)

        self._depth     = depth
        self._addr_bits = addr_bits
        self._mask      = depth - 1
        super().__init__()

    def rtl(self):
        mask      = self._mask
        addr_bits = self._addr_bits
        mask_full = (1 << addr_bits) - 1
        full_check = 3 << (addr_bits - 2)

        @self.comb
        def flags():
            self.rdata = self.mem[int(self.rptr) & mask]
            self.empty = 1 if int(self.rgray) == int(self.wgray_s2) else 0
            wg = int(self.wgray)
            rg_s = int(self.rgray_s2)
            full_cond = (wg ^ rg_s) == (3 << (addr_bits - 2))
            self.full = 1 if full_cond else 0

        flags._veripy_emit_source = (
            f'def flags():\n'
            f'    self.rdata = self.mem[self.rptr & {mask}]\n'
            f'    self.empty = 1 if self.rgray == self.wgray_s2 else 0\n'
            f'    self.full = 1 if (self.wgray ^ self.rgray_s2) == {full_check} else 0\n'
        )

        @self.posedge(self.wclk)
        def write_logic():
            if self.wrst:
                self.wptr._val    = 0
                self.wgray._val   = 0
                self.rgray_s1._val = 0
                self.rgray_s2._val = 0
            else:
                self.rgray_s2._val = int(self.rgray_s1)
                self.rgray_s1._val = int(self.rgray)
                if self.wen and not int(self.full):
                    wp = int(self.wptr)
                    self.mem.write(wp & mask, int(self.wdata))
                    new_wp = (wp + 1) & mask_full
                    self.wptr._val  = new_wp
                    self.wgray._val = new_wp ^ (new_wp >> 1)

        write_logic._veripy_emit_source = (
            f'def write_logic():\n'
            f'    if self.wrst:\n'
            f'        self.wptr    = 0\n'
            f'        self.wgray   = 0\n'
            f'        self.rgray_s1 = 0\n'
            f'        self.rgray_s2 = 0\n'
            f'    else:\n'
            f'        self.rgray_s2 = self.rgray_s1\n'
            f'        self.rgray_s1 = self.rgray\n'
            f'        if self.wen and not self.full:\n'
            f'            self.mem.write(self.wptr & {mask}, self.wdata)\n'
            f'            self.wptr  = (self.wptr + 1) & {mask_full}\n'
            f'            self.wgray = ((self.wptr + 1) & {mask_full}) ^ (((self.wptr + 1) & {mask_full}) >> 1)\n'
        )

        @self.posedge(self.rclk)
        def read_logic():
            if self.rrst:
                self.rptr._val    = 0
                self.rgray._val   = 0
                self.wgray_s1._val = 0
                self.wgray_s2._val = 0
            else:
                self.wgray_s2._val = int(self.wgray_s1)
                self.wgray_s1._val = int(self.wgray)
                if self.ren and not int(self.empty):
                    rp = int(self.rptr)
                    new_rp = (rp + 1) & mask_full
                    self.rptr._val  = new_rp
                    self.rgray._val = new_rp ^ (new_rp >> 1)

        read_logic._veripy_emit_source = (
            f'def read_logic():\n'
            f'    if self.rrst:\n'
            f'        self.rptr    = 0\n'
            f'        self.rgray   = 0\n'
            f'        self.wgray_s1 = 0\n'
            f'        self.wgray_s2 = 0\n'
            f'    else:\n'
            f'        self.wgray_s2 = self.wgray_s1\n'
            f'        self.wgray_s1 = self.wgray\n'
            f'        if self.ren and not self.empty:\n'
            f'            self.rptr  = (self.rptr + 1) & {mask_full}\n'
            f'            self.rgray = ((self.rptr + 1) & {mask_full}) ^ (((self.rptr + 1) & {mask_full}) >> 1)\n'
        )
