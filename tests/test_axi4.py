"""Tests for AXI4 full subordinate IP."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register
from veripy.axi4 import Axi4Bus, Axi4Sub, Axi4Crossbar, RESP_OKAY, BURST_INCR
from veripy.verify import TestBench, initial

T = 10


def _make_sub(depth=16):
    return Axi4Sub(depth=depth, data_width=32, addr_width=32, id_width=4)


class TestAxi4Bus(unittest.TestCase):
    def test_signals_created(self):
        bus = Axi4Bus()
        sigs = bus._signals()
        for name in ('awid', 'awaddr', 'awlen', 'awsize', 'awburst', 'awvalid', 'awready',
                     'wdata', 'wstrb', 'wlast', 'wvalid', 'wready',
                     'bid', 'bresp', 'bvalid', 'bready',
                     'arid', 'araddr', 'arlen', 'arsize', 'arburst', 'arvalid', 'arready',
                     'rid', 'rdata', 'rresp', 'rlast', 'rvalid', 'rready'):
            self.assertIn(name, sigs, f'missing signal: {name}')

    def test_custom_widths(self):
        bus = Axi4Bus(data_width=64, addr_width=16, id_width=8)
        self.assertEqual(bus.wdata.width, 64)
        self.assertEqual(bus.araddr.width, 16)
        self.assertEqual(bus.wstrb.width, 8)
        self.assertEqual(bus.awid.width, 8)


class TestAxi4SubConstruction(unittest.TestCase):
    def test_is_module(self):
        self.assertIsInstance(_make_sub(), Module)

    def test_bus_attached(self):
        self.assertIsInstance(_make_sub().bus, Axi4Bus)

    def test_signals_flattened(self):
        sigs = _make_sub()._signals()
        self.assertIn('bus_awaddr', sigs)
        self.assertIn('bus_rdata', sigs)
        self.assertIn('bus_wlast', sigs)
        self.assertIn('bus_rlast', sigs)


class TestAxi4SubSim(TestBench):
    def create_module(self): return _make_sub()

    def test_single_write_read(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T * 4; dut.reset = 0; yield T
            # Write
            dut.bus_awid = 1; dut.bus_awaddr = 0; dut.bus_awlen = 0
            dut.bus_awsize = 2; dut.bus_awburst = BURST_INCR; dut.bus_awvalid = 1
            yield T; dut.bus_awvalid = 0
            dut.bus_wdata = 0xDEADBEEF; dut.bus_wstrb = 0xF; dut.bus_wlast = 1; dut.bus_wvalid = 1
            yield T; dut.bus_wvalid = 0; dut.bus_wlast = 0
            dut.bus_bready = 1; yield T; dut.bus_bready = 0; yield T
            # Read
            dut.bus_arid = 1; dut.bus_araddr = 0; dut.bus_arlen = 0
            dut.bus_arsize = 2; dut.bus_arburst = BURST_INCR; dut.bus_arvalid = 1
            yield T; dut.bus_arvalid = 0
            self.assertEqual(self.get('bus_rdata'), 0xDEADBEEF)
            self.assertEqual(self.get('bus_rresp'), RESP_OKAY)
            self.assertEqual(self.get('bus_rlast'), 1)
            self.assertEqual(self.get('bus_rvalid'), 1)
            dut.bus_rready = 1; yield T; dut.bus_rready = 0

    def test_burst_write_read(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T * 4; dut.reset = 0; yield T
            dut.bus_awid = 2; dut.bus_awaddr = 0; dut.bus_awlen = 3
            dut.bus_awsize = 2; dut.bus_awburst = BURST_INCR; dut.bus_awvalid = 1
            yield T; dut.bus_awvalid = 0
            for i, val in enumerate([0x11111111, 0x22222222, 0x33333333, 0x44444444]):
                dut.bus_wdata = val; dut.bus_wstrb = 0xF
                dut.bus_wlast = 1 if i == 3 else 0; dut.bus_wvalid = 1
                yield T
            dut.bus_wvalid = 0; dut.bus_wlast = 0
            dut.bus_bready = 1; yield T * 2; dut.bus_bready = 0; yield T
            dut.bus_arid = 2; dut.bus_araddr = 0; dut.bus_arlen = 3
            dut.bus_arsize = 2; dut.bus_arburst = BURST_INCR; dut.bus_arvalid = 1
            yield T; dut.bus_arvalid = 0
            dut.bus_rready = 1
            beats = []
            for i in range(4):
                beats.append(self.get('bus_rdata'))
                if i == 3:
                    self.assertEqual(self.get('bus_rlast'), 1)
                yield T
            dut.bus_rready = 0
            self.assertEqual(beats, [0x11111111, 0x22222222, 0x33333333, 0x44444444])

    def test_write_response_handshake(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T * 4; dut.reset = 0; yield T
            dut.bus_awaddr = 0; dut.bus_awlen = 0; dut.bus_awsize = 2
            dut.bus_awburst = BURST_INCR; dut.bus_awvalid = 1
            yield T; dut.bus_awvalid = 0
            dut.bus_wdata = 0xAB; dut.bus_wstrb = 0xF; dut.bus_wlast = 1; dut.bus_wvalid = 1
            yield T; dut.bus_wvalid = 0; dut.bus_wlast = 0
            yield T
            self.assertEqual(self.get('bus_bvalid'), 1)
            self.assertEqual(self.get('bus_bresp'), RESP_OKAY)
            dut.bus_bready = 1; yield T
            self.assertEqual(self.get('bus_bvalid'), 0)
            dut.bus_bready = 0

    def test_reset_clears_state(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T * 4; dut.reset = 0; yield T
            self.assertEqual(self.get('bus_awready'), 1)
            self.assertEqual(self.get('bus_arready'), 1)

    def test_independent_read_write(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T * 4; dut.reset = 0; yield T
            dut.bus_awaddr = 4; dut.bus_awlen = 0; dut.bus_awsize = 2
            dut.bus_awburst = BURST_INCR; dut.bus_awvalid = 1
            yield T; dut.bus_awvalid = 0
            dut.bus_wdata = 0xCAFEBABE; dut.bus_wstrb = 0xF; dut.bus_wlast = 1; dut.bus_wvalid = 1
            yield T; dut.bus_wvalid = 0; dut.bus_wlast = 0
            dut.bus_bready = 1; yield T; dut.bus_bready = 0; yield T
            dut.bus_araddr = 4; dut.bus_arlen = 0; dut.bus_arsize = 2
            dut.bus_arburst = BURST_INCR; dut.bus_arvalid = 1
            yield T; dut.bus_arvalid = 0
            self.assertEqual(self.get('bus_rdata'), 0xCAFEBABE)
            dut.bus_rready = 1; yield T; dut.bus_rready = 0


class TestAxi4Crossbar(TestBench):
    def create_module(self):
        return Axi4Crossbar(
            addr_map=[(0x0000, 0x1000), (0x1000, 0x1000)],
            data_width=32, addr_width=16, id_width=4,
        )

    def test_construction(self):
        from veripy.axi4 import Axi4Bus
        self.assertIsInstance(self._mod.m_bus, Axi4Bus)
        self.assertIsInstance(self._mod.s_bus_0, Axi4Bus)
        self.assertIsInstance(self._mod.s_bus_1, Axi4Bus)

    def test_signals_flattened(self):
        sigs = self._mod._signals()
        self.assertIn('m_bus_awaddr', sigs)
        self.assertIn('s_bus_0_awvalid', sigs)
        self.assertIn('s_bus_1_arvalid', sigs)

    def test_write_route_to_sub0(self):
        @initial
        def _():
            self.set(m_bus_awaddr=0x0100, m_bus_awvalid=1, m_bus_awid=3,
                     m_bus_awlen=0, m_bus_awsize=2, m_bus_awburst=BURST_INCR)
            yield 1
            self.assertEqual(self.get('s_bus_0_awvalid'), 1)
            self.assertEqual(self.get('s_bus_1_awvalid'), 0)
            self.assertEqual(self.get('s_bus_0_awaddr'), 0x0100)

    def test_write_route_to_sub1(self):
        @initial
        def _():
            self.set(m_bus_awaddr=0x1200, m_bus_awvalid=1,
                     m_bus_awlen=0, m_bus_awsize=2, m_bus_awburst=BURST_INCR)
            yield 1
            self.assertEqual(self.get('s_bus_0_awvalid'), 0)
            self.assertEqual(self.get('s_bus_1_awvalid'), 1)
            self.assertEqual(self.get('s_bus_1_awaddr'), 0x1200)

    def test_read_route_to_sub0(self):
        @initial
        def _():
            self.set(m_bus_araddr=0x0500, m_bus_arvalid=1,
                     m_bus_arlen=0, m_bus_arsize=2, m_bus_arburst=BURST_INCR)
            yield 1
            self.assertEqual(self.get('s_bus_0_arvalid'), 1)
            self.assertEqual(self.get('s_bus_1_arvalid'), 0)

    def test_out_of_range_decerr(self):
        @initial
        def _():
            self.set(m_bus_awaddr=0x9000, m_bus_awvalid=1)
            yield 1
            self.assertEqual(self.get('m_bus_awready'), 0)
            self.assertEqual(self.get('m_bus_bresp'), 3)


if __name__ == '__main__':
    unittest.main()
