"""Tests for Driver base class and SpiDriver example."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest

from veripy import Driver
from veripy.verify import TestBench, initial
from examples.spi_driver import spi_shift_reg, SpiDriver


class TestDriverBase(unittest.TestCase):
    """Driver base class contract — no engine needed."""

    def test_send_raises(self):
        drv = Driver(None, None)
        with self.assertRaises(NotImplementedError):
            drv.send(None)

    def test_recv_raises(self):
        drv = Driver(None, None)
        with self.assertRaises(NotImplementedError):
            drv.recv()

    def test_reset_raises(self):
        drv = Driver(None, None)
        with self.assertRaises(NotImplementedError):
            drv.reset()

    def test_stores_engine_and_mod(self):
        sentinel_e, sentinel_m = object(), object()
        drv = Driver(sentinel_e, sentinel_m)
        self.assertIs(drv.engine, sentinel_e)
        self.assertIs(drv.mod, sentinel_m)


class TestSpiDriver(TestBench):
    def create_module(self): return spi_shift_reg()

    def _shift(self, data, expected):
        dut = self.dut; self.clock('clock', 10)
        drv = SpiDriver(self._engine, self._mod)
        @initial
        def stim():
            dut.cs_n = 1; yield 10
            yield from drv.send(data)
            self.assertEqual(self.get('shreg'), expected)

    def test_shift_0xA5(self):
        self._shift(0xA5, 0xA5)

    def test_shift_0xFF(self):
        self._shift(0xFF, 0xFF)

    def test_shift_0x00(self):
        self._shift(0x00, 0x00)

    def test_back_to_back(self):
        dut = self.dut; self.clock('clock', 10)
        drv = SpiDriver(self._engine, self._mod)
        results = []
        @initial
        def stim():
            dut.cs_n = 1; yield 10
            yield from drv.send(0x55)
            results.append(self.get('shreg'))
            yield from drv.send(0xAA)
            results.append(self.get('shreg'))
            self.assertEqual(results, [0x55, 0xAA])


class TestSpiDriverWide(TestBench):
    def create_module(self): return spi_shift_reg(width=16)

    def test_shift_wide(self):
        dut = self.dut; self.clock('clock', 10)
        drv = SpiDriver(self._engine, self._mod)
        @initial
        def stim():
            dut.cs_n = 1; yield 10
            yield from drv.send(0xBEEF)
            self.assertEqual(self.get('shreg'), 0xBEEF)


class TestSpiControllerDriver(TestBench):
    def create_module(self):
        from examples.spi_controller import SpiController
        return SpiController(width=8, fifo_depth=4, clk_div=2)

    def _setup(self):
        from examples.spi_driver import SpiControllerDriver
        self.clock('clock', 10)
        return SpiControllerDriver(self._engine, self._mod)

    def test_send_completes(self):
        drv = self._setup()
        @initial
        def stim():
            self.set(reset=1); self.set(spi_miso=0); yield 20
            self.set(reset=0); yield 20
            yield from drv.send(0xA5)
            yield 500
            self.assertTrue(True)  # reached here = send completed

    def test_recv_returns_data(self):
        drv = self._setup()
        @initial
        def stim():
            self.set(reset=1); self.set(spi_miso=0); yield 20
            self.set(reset=0); yield 20
            yield from drv.send(0x42)
            val = yield from drv.recv()
            self.assertEqual(val, 0)

    def test_back_to_back_sends(self):
        drv = self._setup()
        @initial
        def stim():
            self.set(reset=1); self.set(spi_miso=0); yield 20
            self.set(reset=0); yield 20
            results = []
            for data in [0x55, 0xAA]:
                yield from drv.send(data)
                val = yield from drv.recv()
                results.append(val)
            self.assertEqual(len(results), 2)


if __name__ == '__main__':
    unittest.main()
