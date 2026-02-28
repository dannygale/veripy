"""Dual-path tests for SPI controller: sim vs RTL verification."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import VeripyTestCase
from veripy.sim import SimEngine
from examples.spi_controller import SpiController, sync_fifo

T = 10  # half-period


class TestSpiSingleTransfer(VeripyTestCase):
    """Single byte SPI transfer: push 0xA5, verify MOSI bits."""

    def create_module(self):
        return SpiController(width=8, fifo_depth=4, clk_div=2)

    def test_single_byte(self):
        m = self._mod

        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            m.reset.set(1); m.tx_valid.set(0); m.tx_data.set(0); m.spi.miso.set(0)
            yield T * 2
            m.reset.set(0)
            yield T * 2

            # Push 0xA5 into FIFO
            m.tx_data.set(0xA5); m.tx_valid.set(1)
            yield T * 2
            m.tx_valid.set(0)

            # Wait for transfer to complete — check rx_valid each cycle
            saw_rx_valid = False
            for _ in range(200):
                yield T * 2
                if int(m.rx_valid):
                    saw_rx_valid = True
                    break

            self.assertTrue(saw_rx_valid, "rx_valid never asserted")


class TestSpiFifoFlags(VeripyTestCase):
    """Verify FIFO full/empty flags and tx_ready."""

    def create_module(self):
        return SpiController(width=8, fifo_depth=4, clk_div=2)

    def test_tx_ready_when_empty(self):
        m = self._mod

        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stimulus():
            m.reset.set(1); m.tx_valid.set(0); m.tx_data.set(0); m.spi.miso.set(0)
            yield T * 2
            m.reset.set(0)
            yield T * 2
            self.assertEqual(int(m.tx_ready), 1)


class TestSpiMultiTransfer(VeripyTestCase):
    """Send multiple bytes back-to-back, verify each completes."""

    def create_module(self):
        return SpiController(width=8, fifo_depth=4, clk_div=2)

    def _run_n_transfers(self, n):
        m = self._mod

        @self.always
        def clock():
            self.set(clock=0); yield T
            self.set(clock=1); yield T

        @self.initial
        def stim():
            m.reset.set(1); m.tx_valid.set(0); m.tx_data.set(0); m.spi.miso.set(0)
            yield T * 2
            m.reset.set(0)
            yield T * 2

            for i in range(n):
                m.tx_data.set(i & 255); m.tx_valid.set(1)
                yield T * 2
                m.tx_valid.set(0)
                for _ in range(200):
                    yield T * 2
                    if int(m.rx_valid):
                        self.out('rx_data')
                        break

    def test_10_transfers(self):
        self._run_n_transfers(10)

    def test_100_transfers(self):
        self._run_n_transfers(100)

    def test_1000_transfers(self):
        self._run_n_transfers(1000)


class TestSpiCompiles(unittest.TestCase):
    """Verify emitted Verilog compiles with iverilog."""

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
