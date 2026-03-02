"""Tests for Driver base class and SpiDriver example."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest

from veripy import Driver
from veripy.sim import SimEngine
from examples.spi_driver import spi_shift_reg, SpiDriver


class TestDriverBase(unittest.TestCase):
    """Driver base class contract."""

    def test_send_raises(self):
        dut = spi_shift_reg()
        sim = SimEngine(dut)
        drv = Driver(sim, dut)
        with self.assertRaises(NotImplementedError):
            drv.send(None)

    def test_recv_raises(self):
        dut = spi_shift_reg()
        sim = SimEngine(dut)
        drv = Driver(sim, dut)
        with self.assertRaises(NotImplementedError):
            drv.recv()

    def test_reset_raises(self):
        dut = spi_shift_reg()
        sim = SimEngine(dut)
        drv = Driver(sim, dut)
        with self.assertRaises(NotImplementedError):
            drv.reset()

    def test_stores_engine_and_mod(self):
        dut = spi_shift_reg()
        sim = SimEngine(dut)
        drv = Driver(sim, dut)
        self.assertIs(drv.engine, sim)
        self.assertIs(drv.mod, dut)


class TestSpiDriver(unittest.TestCase):
    """SpiDriver shifts data correctly through the sim engine."""

    def _run_shift(self, data, width=8):
        dut = spi_shift_reg(width=width)
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        drv = SpiDriver(sim, dut)
        result = []

        @sim.initial
        def stim():
            dut.cs_n.set(1)
            yield 10
            yield from drv.send(data)
            result.append(int(dut.shreg))

        sim.run()
        return result[0]

    def test_shift_0xA5(self):
        self.assertEqual(self._run_shift(0xA5), 0xA5)

    def test_shift_0xFF(self):
        self.assertEqual(self._run_shift(0xFF), 0xFF)

    def test_shift_0x00(self):
        self.assertEqual(self._run_shift(0x00), 0x00)

    def test_shift_wide(self):
        self.assertEqual(self._run_shift(0xBEEF, width=16), 0xBEEF)

    def test_back_to_back(self):
        """Two consecutive sends overwrite the shift register."""
        dut = spi_shift_reg()
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        drv = SpiDriver(sim, dut)
        results = []

        @sim.initial
        def stim():
            dut.cs_n.set(1)
            yield 10
            yield from drv.send(0x55)
            results.append(int(dut.shreg))
            yield from drv.send(0xAA)
            results.append(int(dut.shreg))

        sim.run()
        self.assertEqual(results, [0x55, 0xAA])


class TestSpiControllerDriver(unittest.TestCase):
    """SpiControllerDriver uses reactive yields (until) for handshaking."""

    def _make(self):
        from examples.spi_controller import SpiController
        from examples.spi_driver import SpiControllerDriver
        dut = SpiController(width=8, fifo_depth=4, clk_div=2)
        sim = SimEngine(dut)
        sim.clock(dut.clock, 10)
        drv = SpiControllerDriver(sim, dut)
        return dut, sim, drv

    def test_send_completes(self):
        """send() waits for tx_ready via until(), then pushes data."""
        dut, sim, drv = self._make()
        done = []

        @sim.initial
        def stim():
            dut.reset.set(1); dut.spi.miso.set(0)
            yield 20
            dut.reset.set(0)
            yield 20
            yield from drv.send(0xA5)
            done.append(True)
            # let transfer finish
            yield 500

        sim.run()
        self.assertTrue(done)

    def test_recv_returns_data(self):
        """recv() waits for rx_valid via until(), returns rx_data."""
        dut, sim, drv = self._make()
        result = []

        @sim.initial
        def stim():
            dut.reset.set(1); dut.spi.miso.set(0)
            yield 20
            dut.reset.set(0)
            yield 20
            yield from drv.send(0x42)
            val = yield from drv.recv()
            result.append(val)

        sim.run()
        self.assertEqual(len(result), 1)
        # MISO tied to 0 → rx_data is 0
        self.assertEqual(result[0], 0)

    def test_back_to_back_sends(self):
        """Two consecutive send+recv cycles complete."""
        dut, sim, drv = self._make()
        results = []

        @sim.initial
        def stim():
            dut.reset.set(1); dut.spi.miso.set(0)
            yield 20
            dut.reset.set(0)
            yield 20
            for data in [0x55, 0xAA]:
                yield from drv.send(data)
                val = yield from drv.recv()
                results.append(val)

        sim.run()
        self.assertEqual(len(results), 2)


if __name__ == '__main__':
    unittest.main()
