"""Tests for AXI4 full subordinate IP."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register
from veripy.axi4 import Axi4Bus, Axi4Sub, RESP_OKAY, BURST_INCR
from veripy.sim import SimEngine

T = 10  # time units per half-period (posedge every T units)


def _make_sub(depth=16):
    return Axi4Sub(depth=depth, data_width=32, addr_width=32, id_width=4)


def _run(sub, stim_fn):
    sim = SimEngine(sub)
    sim.clock(sub.clock, T)
    results = {}
    sim.initial(lambda: stim_fn(sub, results))
    sim.run()
    return results


# ── Interface construction tests ─────────────────────────────────────

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
        self.assertEqual(bus.wstrb.width, 8)   # 64/8
        self.assertEqual(bus.awid.width, 8)


# ── Module construction tests ────────────────────────────────────────

class TestAxi4SubConstruction(unittest.TestCase):
    def test_is_module(self):
        sub = _make_sub()
        self.assertIsInstance(sub, Module)

    def test_bus_attached(self):
        sub = _make_sub()
        self.assertIsInstance(sub.bus, Axi4Bus)

    def test_signals_flattened(self):
        sub = _make_sub()
        sigs = sub._signals()
        self.assertIn('bus_awaddr', sigs)
        self.assertIn('bus_rdata', sigs)
        self.assertIn('bus_wlast', sigs)
        self.assertIn('bus_rlast', sigs)


# ── Simulation helpers ───────────────────────────────────────────────

def _write_word(s, addr, data, awid=0):
    """Drive a single-beat AXI4 write transaction."""
    s.bus.awid.set(awid); s.bus.awaddr.set(addr); s.bus.awlen.set(0)
    s.bus.awsize.set(2); s.bus.awburst.set(BURST_INCR); s.bus.awvalid.set(1)
    yield T                                    # posedge latches AW channel
    s.bus.awvalid.set(0)
    s.bus.wdata.set(data); s.bus.wstrb.set(0xF); s.bus.wlast.set(1); s.bus.wvalid.set(1)
    yield T                                    # posedge writes mem, → WRESP
    s.bus.wvalid.set(0); s.bus.wlast.set(0)
    s.bus.bready.set(1)
    yield T                                    # posedge clears WRESP → IDLE
    s.bus.bready.set(0)
    yield T                                    # settle


def _read_word(s, addr, arid=0):
    """Drive a single-beat AXI4 read; returns rdata."""
    s.bus.arid.set(arid); s.bus.araddr.set(addr); s.bus.arlen.set(0)
    s.bus.arsize.set(2); s.bus.arburst.set(BURST_INCR); s.bus.arvalid.set(1)
    yield T                                    # posedge latches AR channel → RDATA
    s.bus.arvalid.set(0)
    # rdata/rvalid/rlast are combinational — read before asserting rready
    val = int(s.bus.rdata)
    s.bus.rready.set(1)
    yield T                                    # posedge advances burst → IDLE
    s.bus.rready.set(0)
    return val


# ── Simulation tests ─────────────────────────────────────────────────

class TestAxi4SubSim(unittest.TestCase):

    def test_single_write_read(self):
        """Write one word then read it back."""
        def stim(s, r):
            s.reset.set(1); yield T * 4
            s.reset.set(0); yield T

            yield from _write_word(s, addr=0, data=0xDEADBEEF, awid=1)

            s.bus.arid.set(1); s.bus.araddr.set(0); s.bus.arlen.set(0)
            s.bus.arsize.set(2); s.bus.arburst.set(BURST_INCR); s.bus.arvalid.set(1)
            yield T                            # posedge → RDATA
            s.bus.arvalid.set(0)
            r['rdata']  = int(s.bus.rdata)
            r['rresp']  = int(s.bus.rresp)
            r['rlast']  = int(s.bus.rlast)
            r['rvalid'] = int(s.bus.rvalid)
            s.bus.rready.set(1)
            yield T
            s.bus.rready.set(0)

        r = _run(_make_sub(), stim)
        self.assertEqual(r['rdata'],  0xDEADBEEF)
        self.assertEqual(r['rresp'],  RESP_OKAY)
        self.assertEqual(r['rlast'],  1)
        self.assertEqual(r['rvalid'], 1)

    def test_burst_write_read(self):
        """4-beat INCR burst write then read back."""
        def stim(s, r):
            s.reset.set(1); yield T * 4
            s.reset.set(0); yield T

            # Burst write: 4 beats starting at address 0
            s.bus.awid.set(2); s.bus.awaddr.set(0); s.bus.awlen.set(3)
            s.bus.awsize.set(2); s.bus.awburst.set(BURST_INCR); s.bus.awvalid.set(1)
            yield T                            # posedge → WDATA
            s.bus.awvalid.set(0)
            for i, val in enumerate([0x11111111, 0x22222222, 0x33333333, 0x44444444]):
                s.bus.wdata.set(val); s.bus.wstrb.set(0xF)
                s.bus.wlast.set(1 if i == 3 else 0); s.bus.wvalid.set(1)
                yield T                        # posedge writes beat i
            s.bus.wvalid.set(0); s.bus.wlast.set(0)
            s.bus.bready.set(1); yield T * 2
            s.bus.bready.set(0); yield T

            # Burst read: 4 beats starting at address 0
            s.bus.arid.set(2); s.bus.araddr.set(0); s.bus.arlen.set(3)
            s.bus.arsize.set(2); s.bus.arburst.set(BURST_INCR); s.bus.arvalid.set(1)
            yield T                            # posedge → RDATA, rbeat=0
            s.bus.arvalid.set(0)
            s.bus.rready.set(1)
            beats = []
            for i in range(4):
                beats.append(int(s.bus.rdata))  # read beat i before posedge advances
                if i == 3:
                    r['rlast'] = int(s.bus.rlast)
                yield T                        # posedge advances rbeat
            r['beats'] = beats
            s.bus.rready.set(0)

        r = _run(_make_sub(), stim)
        self.assertEqual(r['beats'], [0x11111111, 0x22222222, 0x33333333, 0x44444444])
        self.assertEqual(r['rlast'], 1)

    def test_write_response_handshake(self):
        """bvalid asserted after wlast; clears after bready."""
        def stim(s, r):
            s.reset.set(1); yield T * 4
            s.reset.set(0); yield T

            s.bus.awaddr.set(0); s.bus.awlen.set(0); s.bus.awsize.set(2)
            s.bus.awburst.set(BURST_INCR); s.bus.awvalid.set(1)
            yield T; s.bus.awvalid.set(0)
            s.bus.wdata.set(0xAB); s.bus.wstrb.set(0xF); s.bus.wlast.set(1); s.bus.wvalid.set(1)
            yield T; s.bus.wvalid.set(0); s.bus.wlast.set(0)
            yield T                            # now in WRESP
            r['bvalid_before'] = int(s.bus.bvalid)
            r['bresp']         = int(s.bus.bresp)
            s.bus.bready.set(1); yield T
            r['bvalid_after']  = int(s.bus.bvalid)
            s.bus.bready.set(0)

        r = _run(_make_sub(), stim)
        self.assertEqual(r['bvalid_before'], 1)
        self.assertEqual(r['bresp'],         RESP_OKAY)
        self.assertEqual(r['bvalid_after'],  0)

    def test_reset_clears_state(self):
        """After reset, awready and arready are asserted (IDLE)."""
        def stim(s, r):
            s.reset.set(1); yield T * 4
            s.reset.set(0); yield T
            r['awready'] = int(s.bus.awready)
            r['arready'] = int(s.bus.arready)

        r = _run(_make_sub(), stim)
        self.assertEqual(r['awready'], 1)
        self.assertEqual(r['arready'], 1)

    def test_independent_read_write(self):
        """Write to word 1, then read it back."""
        def stim(s, r):
            s.reset.set(1); yield T * 4
            s.reset.set(0); yield T

            yield from _write_word(s, addr=4, data=0xCAFEBABE)

            s.bus.araddr.set(4); s.bus.arlen.set(0); s.bus.arsize.set(2)
            s.bus.arburst.set(BURST_INCR); s.bus.arvalid.set(1)
            yield T                            # posedge → RDATA
            s.bus.arvalid.set(0)
            r['rdata'] = int(s.bus.rdata)
            s.bus.rready.set(1); yield T
            s.bus.rready.set(0)

        r = _run(_make_sub(), stim)
        self.assertEqual(r['rdata'], 0xCAFEBABE)


if __name__ == '__main__':
    unittest.main()
