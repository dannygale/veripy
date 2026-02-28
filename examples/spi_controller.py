"""Pipelined SPI controller with command FIFO.

Exercises: @module, class-based Module, FSM, Mem, Interface, sub-modules,
parametric widths, formal properties, timing constraints, dual-path testing.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import (Module, Input, Output, Register, Signal, Mem,
                    Interface, module, posedge, negedge)
from veripy.context import comb, always


# ── SPI bus interface bundle ─────────────────────────────────────────

class SpiBus(Interface):
    sclk = ('output', 1)
    mosi = ('output', 1)
    miso = ('input',  1)
    cs_n = ('output', 1)


# ── FIFO sub-module (@module style) ─────────────────────────────────

@module
def sync_fifo(width=8, depth=4):
    """Synchronous FIFO with Mem storage."""
    clock = Input()
    reset = Input()

    # Push side
    push    = Input()
    din     = Input(width)
    full    = Output()

    # Pop side
    pop     = Input()
    dout    = Output(width)
    empty   = Output()

    mem     = Mem(depth, width)
    wptr    = Register(3)   # extra bit for full/empty detection
    rptr    = Register(3)
    cnt     = Register(4)

    @comb
    def flags():
        full  = 1 if cnt == depth else 0
        empty = 1 if cnt == 0 else 0
        dout  = mem[rptr[1:0]]

    @always(posedge(clock))
    def update():
        if reset:
            wptr  = 0
            rptr  = 0
            cnt   = 0
        else:
            if push and cnt != depth:
                mem.write(wptr[1:0], din)
                wptr  = wptr + 1
                cnt   = cnt + 1
            if pop and cnt != 0:
                rptr  = rptr + 1
                cnt   = cnt - 1


# ── SPI controller (class-based) ────────────────────────────────────

class SpiController(Module):
    """SPI master with TX FIFO. Shifts MSB-first, CPOL=0 CPHA=0."""

    def __init__(self, width=8, fifo_depth=4, clk_div=4):
        # System
        self.clock = Input()
        self.reset = Input()

        # Host interface
        self.tx_data  = Input(width)
        self.tx_valid = Input()
        self.tx_ready = Output()
        self.rx_data  = Output(width)
        self.rx_valid = Output()

        # SPI bus
        self.spi = SpiBus()

        # Sub-module: TX FIFO
        self.fifo = sync_fifo(width=width, depth=fifo_depth)

        # Internal
        self.shift_out = Register(width)
        self.shift_in  = Register(width)
        self.bit_cnt   = Register(4)
        self.clk_cnt   = Register(8)
        self.sclk_reg  = Register()

        super().__init__()

        # ── Timing constraints ───────────────────────────────────
        self.create_clock(self.clock, period_ns=10)
        self.max_delay(self.tx_data, self.spi.mosi, ns=8)

        # ── FIFO wiring ──────────────────────────────────────────
        @self.comb
        def fifo_wiring():
            self.fifo.clock = self.clock
            self.fifo.reset = self.reset
            self.fifo.din   = self.tx_data
            self.fifo.push  = self.tx_valid and not self.fifo.full
            self.tx_ready   = 1 if not self.fifo.full else 0

        # ── FSM ──────────────────────────────────────────────────
        @self.fsm(self.clock, self.reset,
                  states=['IDLE', 'LOAD', 'SHIFT', 'DONE'])
        def ctrl(state):
            self.rx_valid    = 0
            self.fifo.pop    = 0
            self.spi.cs_n    = 1
            self.spi.sclk    = 0
            self.spi.mosi    = 0

            if state == IDLE:
                if not self.fifo.empty:
                    self.fifo.pop = 1
                    return LOAD

            elif state == LOAD:
                self.spi.cs_n = 0
                return SHIFT

            elif state == SHIFT:
                self.spi.cs_n = 0
                self.spi.sclk = self.sclk_reg
                self.spi.mosi = self.shift_out[width - 1]
                if self.bit_cnt == width and self.clk_cnt == 0:
                    return DONE

            elif state == DONE:
                self.rx_valid = 1
                return IDLE

        # ── Shift register + clock divider ────────────────────────
        @self.posedge(self.clock)
        def shift_logic():
            if self.reset:
                self.shift_out = 0
                self.shift_in  = 0
                self.bit_cnt   = 0
                self.clk_cnt   = 0
                self.sclk_reg  = 0
            elif self._fsm_state == 1:       # LOAD
                self.shift_out = self.fifo.dout
                self.shift_in  = 0
                self.bit_cnt   = 0
                self.clk_cnt   = 0
                self.sclk_reg  = 0
            elif self._fsm_state == 2:       # SHIFT
                if self.clk_cnt == clk_div - 1:
                    self.clk_cnt  = 0
                    self.sclk_reg = not self.sclk_reg
                    if self.sclk_reg:        # falling edge of sclk
                        self.shift_out = self.shift_out << 1
                        self.shift_in  = (self.shift_in << 1) | self.spi.miso
                        self.bit_cnt   = self.bit_cnt + 1
                else:
                    self.clk_cnt = self.clk_cnt + 1

        # ── RX output ────────────────────────────────────────────
        @self.comb
        def rx_out():
            self.rx_data = self.shift_in

        # ── Formal properties ────────────────────────────────────
        @self.assert_always(self.clock)
        def cs_during_shift():
            """CS must be low during SHIFT state."""
            if int(self._fsm_state) == 2:    # SHIFT
                return int(self.spi.cs_n) == 0
            return True

        @self.cover(self.clock)
        def full_fifo_transfer():
            """Cover: FIFO was full at some point."""
            return int(self.fifo.full) == 1


# ── Main: emit Verilog + run quick sim ───────────────────────────────

if __name__ == '__main__':
    from veripy.sim import SimEngine

    spi = SpiController(width=8, fifo_depth=4, clk_div=2)

    print("=== FIFO Verilog ===")
    print(spi.fifo.to_verilog())
    print()
    print("=== SPI Controller Verilog ===")
    print(spi.to_verilog())
    print()
    print("=== Timing Constraints (SDC) ===")
    print(spi.to_sdc())
