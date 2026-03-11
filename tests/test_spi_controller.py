"""Tests for SPI controller: sim vs RTL verification."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy.verify import TestBench, initial
from examples.spi_controller import SpiController, sync_fifo

T = 10  # half-period


class TestSpiSingleTransfer(TestBench):
    def create_module(self):
        return SpiController(width=8, fifo_depth=4, clk_div=2)

    def test_single_byte(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.tx_valid = 0; dut.tx_data = 0; dut.spi_miso = 0
            yield T * 2; dut.reset = 0; yield T * 2
            dut.tx_data = 0xA5; dut.tx_valid = 1; yield T * 2
            dut.tx_valid = 0
            saw_rx_valid = False
            for _ in range(200):
                yield T * 2
                if self.get('rx_valid'):
                    saw_rx_valid = True
                    break
            self.assertTrue(saw_rx_valid, "rx_valid never asserted")


class TestSpiFifoFlags(TestBench):
    def create_module(self):
        return SpiController(width=8, fifo_depth=4, clk_div=2)

    def test_tx_ready_when_empty(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.tx_valid = 0; dut.tx_data = 0; dut.spi_miso = 0
            yield T * 2; dut.reset = 0; yield T * 2
            self.assertEqual(self.get('tx_ready'), 1)


class TestSpiMultiTransfer(TestBench):
    def create_module(self):
        return SpiController(width=8, fifo_depth=4, clk_div=2)

    def _run_n_transfers(self, n):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.tx_valid = 0; dut.tx_data = 0; dut.spi_miso = 0
            yield T * 2; dut.reset = 0; yield T * 2
            for i in range(n):
                dut.tx_data = i & 255; dut.tx_valid = 1; yield T * 2
                dut.tx_valid = 0
                for _ in range(200):
                    yield T * 2
                    if self.get('rx_valid'):
                        break

    def test_10_transfers(self):
        self._run_n_transfers(10)

    def test_100_transfers(self):
        self._run_n_transfers(100)

    def test_1000_transfers(self):
        self._run_n_transfers(1000)


class TestSpiCompiles(unittest.TestCase):
    def test_fifo_compiles(self):
        import subprocess, tempfile
        fifo = sync_fifo(width=8, depth=4)
        v = fifo.to_verilog()
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'fifo.v')
            out = os.path.join(d, 'fifo.out')
            with open(src, 'w') as f:
                f.write(v)
            r = subprocess.run(['iverilog', '-o', out, src],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_spi_hierarchy_compiles(self):
        import subprocess, tempfile
        spi = SpiController(width=8, fifo_depth=4, clk_div=2)
        fifo_v = spi.fifo.to_verilog()
        spi_v = spi.to_verilog()
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'spi.v')
            out = os.path.join(d, 'spi.out')
            with open(src, 'w') as f:
                f.write(fifo_v + '\n\n' + spi_v)
            r = subprocess.run(['iverilog', '-o', out, src],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_sdc_output(self):
        spi = SpiController(width=8, fifo_depth=4, clk_div=2)
        sdc = spi.to_sdc()
        self.assertIn('create_clock', sdc)
        self.assertIn('set_max_delay', sdc)

    def test_fsm_state_register(self):
        spi = SpiController(width=8, fifo_depth=4, clk_div=2)
        v = spi.to_verilog()
        self.assertIn('_fsm_state', v)
        self.assertIn('_fsm_next', v)
        self.assertIn('case (_fsm_state)', v)


if __name__ == '__main__':
    unittest.main()
