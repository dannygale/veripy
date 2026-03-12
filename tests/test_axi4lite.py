"""Tests for AXI4-Lite subordinate IP."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
import tempfile
import subprocess
from veripy import Module, Input, Output, Register
from veripy.axi4lite import Axi4LiteBus, Axi4LiteSub, RESP_OKAY, RESP_DECERR
from veripy.verify import TestBench, initial


def _make_sub():
    return Axi4LiteSub(reg_map=[
        (0x00, 'ctrl',   32, 'rw'),
        (0x04, 'status', 32, 'ro'),
        (0x08, 'data',   32, 'rw'),
        (0x0C, 'wonly',  32, 'wo'),
    ])


class TestAxi4LiteBus(unittest.TestCase):
    def test_signals_created(self):
        bus = Axi4LiteBus()
        sigs = bus._signals()
        for name in ('awaddr', 'awvalid', 'awready', 'wdata', 'wstrb',
                     'wvalid', 'wready', 'bresp', 'bvalid', 'bready',
                     'araddr', 'arvalid', 'arready', 'rdata', 'rresp',
                     'rvalid', 'rready', 'awprot', 'arprot'):
            self.assertIn(name, sigs, f'missing signal: {name}')

    def test_custom_widths(self):
        bus = Axi4LiteBus(data_width=64, addr_width=16)
        self.assertEqual(bus.wdata.width, 64)
        self.assertEqual(bus.araddr.width, 16)
        self.assertEqual(bus.wstrb.width, 8)


class TestAxi4LiteSubConstruction(unittest.TestCase):
    def test_is_module(self):
        self.assertIsInstance(_make_sub(), Module)

    def test_registers_created(self):
        sub = _make_sub()
        self.assertEqual(sub.ctrl.width, 32)
        self.assertEqual(sub.status.width, 32)
        self.assertEqual(sub.data.width, 32)
        self.assertEqual(sub.wonly.width, 32)

    def test_bus_attached(self):
        self.assertIsInstance(_make_sub().bus, Axi4LiteBus)

    def test_signals_flattened(self):
        sigs = _make_sub()._signals()
        self.assertIn('bus_awaddr', sigs)
        self.assertIn('bus_rdata', sigs)
        self.assertIn('ctrl', sigs)


class TestAxi4LiteSubSim(TestBench):
    def create_module(self): return _make_sub()

    def test_write_and_read(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awaddr = 0x00; dut.bus_awvalid = 1
            dut.bus_wdata = 0xCAFEBABE; dut.bus_wstrb = 0xF; dut.bus_wvalid = 1
            yield 10
            dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            self.assertEqual(self.get('ctrl'), 0xCAFEBABE)
            dut.bus_araddr = 0x00; dut.bus_arvalid = 1; yield 10
            self.assertEqual(self.get('bus_rdata'), 0xCAFEBABE)
            self.assertEqual(self.get('bus_rvalid'), 1)
            self.assertEqual(self.get('bus_rresp'), RESP_OKAY)

    def test_byte_enable(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awaddr = 0x00; dut.bus_awvalid = 1
            dut.bus_wdata = 0xDEADBEEF; dut.bus_wstrb = 0xF; dut.bus_wvalid = 1
            yield 10; dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            dut.bus_awaddr = 0x00; dut.bus_awvalid = 1
            dut.bus_wdata = 0x000000AA; dut.bus_wstrb = 0x1; dut.bus_wvalid = 1
            yield 10; dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            self.assertEqual(self.get('ctrl'), 0xDEADBEAA)

    def test_decerr_on_invalid_address(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_araddr = 0xFF; dut.bus_arvalid = 1; yield 10
            self.assertEqual(self.get('bus_rresp'), RESP_DECERR)
            self.assertEqual(self.get('bus_rvalid'), 1)

    def test_ro_register_not_writable(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awaddr = 0x04; dut.bus_awvalid = 1
            dut.bus_wdata = 0xFF; dut.bus_wstrb = 0xF; dut.bus_wvalid = 1
            yield 10; dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            self.assertEqual(self.get('status'), 0)

    def test_wo_register_not_readable(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awaddr = 0x0C; dut.bus_awvalid = 1
            dut.bus_wdata = 0xBEEF; dut.bus_wstrb = 0xF; dut.bus_wvalid = 1
            yield 10; dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            dut.bus_araddr = 0x0C; dut.bus_arvalid = 1; yield 10
            self.assertEqual(self.get('bus_rresp'), RESP_DECERR)

    def test_reset_clears_rw_registers(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awaddr = 0x00; dut.bus_awvalid = 1
            dut.bus_wdata = 0xFF; dut.bus_wstrb = 0xF; dut.bus_wvalid = 1
            yield 10; dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            self.assertEqual(self.get('ctrl'), 0)

    def test_handshake_signals(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awvalid = 1; yield 10
            self.assertEqual(self.get('bus_awready'), 1)
            dut.bus_awvalid = 0; yield 10
            self.assertEqual(self.get('bus_awready'), 0)


class TestAxi4LiteSubVerilog(unittest.TestCase):
    def setUp(self):
        self.sub = _make_sub()
        self.v = self.sub.to_verilog()

    def test_module_declaration(self):
        self.assertIn('module axi4litesub', self.v)

    def test_bus_ports(self):
        self.assertIn('input [31:0] bus_awaddr', self.v)
        self.assertIn('[31:0] bus_rdata', self.v)
        self.assertIn('input [3:0] bus_wstrb', self.v)

    def test_register_declarations(self):
        self.assertIn('reg [31:0] ctrl', self.v)
        self.assertIn('reg [31:0] status', self.v)

    def test_address_decode(self):
        self.assertIn('case (bus_araddr)', self.v)

    def test_write_decode(self):
        self.assertIn('case (bus_awaddr)', self.v)

    def test_iverilog_compiles(self):
        with tempfile.NamedTemporaryFile(suffix='.v', mode='w', delete=False) as f:
            f.write(self.v); f.flush()
            r = subprocess.run(['iverilog', '-o', '/dev/null', f.name],
                               capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual(r.returncode, 0, f'iverilog failed:\n{r.stderr}')


if __name__ == '__main__':
    unittest.main()
