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
            # send() should raise immediately (not a generator)
            drv.send(None)

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


if __name__ == '__main__':
    unittest.main()
