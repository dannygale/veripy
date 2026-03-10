"""SPI shift-register example using the Driver base class.

Demonstrates how to subclass ``Driver`` to build a reusable
transaction-level SPI master that shifts data MSB-first.

Usage::

    from veripy.sim import SimEngine

    dut = spi_shift_reg()
    sim = SimEngine(dut)
    sim.clock(dut.clock, 10)
    drv = SpiDriver(sim, dut)

    @sim.initial
    def stim():
        yield from drv.send(0xA5)
        # dut.shreg now contains 0xA5

    sim.run()
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import module, Input, Output, Register, posedge
from veripy.context import comb, always
from veripy.driver import Driver


# ── DUT: simple SPI slave shift register ─────────────────────────────

@module
def spi_shift_reg(width=8):
    """Shift register clocked by sclk, samples mosi on rising edge."""
    clock = Input()       # system clock (unused by shift logic, needed for sim)
    sclk  = Input()
    mosi  = Input()
    cs_n  = Input()
    shreg = Output(width)
    sr    = Register(width)

    @comb
    def out():
        shreg = sr

    @always(posedge(sclk))
    def shift():
        if not cs_n:
            sr = (sr << 1) | mosi


# ── Driver: SPI master ──────────────────────────────────────────────

class SpiDriver(Driver):
    """Transaction-level SPI master.  Shifts *width* bits MSB-first."""

    def __init__(self, engine, mod, half_period=5):
        super().__init__(engine, mod)
        self.half_period = half_period

    def send(self, data):
        """Generator: shift *data* (int) MSB-first onto mosi/sclk."""
        m = self.mod
        hp = self.half_period
        width = m.shreg.width
        m.cs_n.set(0)
        for bit in range(width - 1, -1, -1):
            m.mosi.set((data >> bit) & 1)
            m.sclk.set(0)
            yield hp
            m.sclk.set(1)
            yield hp
        m.sclk.set(0)
        m.cs_n.set(1)
        yield hp  # let final edge propagate


# ── Controller-level driver (reactive yields) ───────────────────────

class SpiControllerDriver(Driver):
    """Transaction-level driver for :class:`SpiController`.

    Uses ``until()`` to wait for tx_ready before pushing data and
    rx_valid after the transfer completes.
    """

    def send(self, data):
        """Generator: push *data* through the SPI controller's host interface."""
        m = self.mod
        from veripy.sim import until
        # wait for controller ready
        yield until(lambda: int(m.tx_ready) == 1)
        m.tx_data.set(data)
        m.tx_valid.set(1)
        yield 10  # one clock cycle
        m.tx_valid.set(0)

    def recv(self):
        """Generator: wait for rx_valid and return received byte."""
        m = self.mod
        from veripy.sim import until
        yield until(lambda: int(m.rx_valid) == 1)
        return int(m.rx_data)


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench


class SpiDriverTestBench(TestBench):

    def create_module(self):
        return spi_shift_reg(width=8)

    def test_shift_byte(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            m = self.module
            drv = SpiDriver(self._engine, m, half_period=10)
            m.cs_n.set(1)
            yield 10
            yield from drv.send(0xA5)
            self.assertEqual(self.out('shreg'), 0xA5)

        self.run_sim()

    def test_shift_zero(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            m = self.module
            drv = SpiDriver(self._engine, m, half_period=10)
            m.cs_n.set(1)
            yield 10
            yield from drv.send(0x00)
            self.assertEqual(self.out('shreg'), 0x00)

        self.run_sim()


# ── Quick demo ───────────────────────────────────────────────────────

if __name__ == '__main__':
    from veripy.sim import SimEngine

    dut = spi_shift_reg(width=8)
    sim = SimEngine(dut)
    sim.clock(dut.clock, 10)
    drv = SpiDriver(sim, dut)

    @sim.initial
    def stim():
        dut.cs_n.set(1)
        yield 10
        yield from drv.send(0xA5)
        print(f'shreg = 0x{int(dut.shreg):02X}')  # expect 0xA5

    sim.run()
