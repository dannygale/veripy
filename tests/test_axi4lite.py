"""Tests for AXI4-Lite subordinate IP."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
import tempfile
import subprocess
from veripy import Module, Input, Output, Register
from veripy.axi4lite import Axi4LiteBus, Axi4LiteSub, RESP_OKAY, RESP_DECERR
from veripy.sim import SimEngine


def _make_sub():
    return Axi4LiteSub(reg_map=[
        (0x00, 'ctrl',   32, 'rw'),
        (0x04, 'status', 32, 'ro'),
        (0x08, 'data',   32, 'rw'),
        (0x0C, 'wonly',  32, 'wo'),
    ])


# ── Interface tests ──────────────────────────────────────────────────

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
        self.assertEqual(bus.wstrb.width, 8)  # 64/8


# ── Module construction tests ────────────────────────────────────────

class TestAxi4LiteSubConstruction(unittest.TestCase):
    def test_is_module(self):
        sub = _make_sub()
        self.assertIsInstance(sub, Module)

    def test_registers_created(self):
        sub = _make_sub()
        self.assertEqual(sub.ctrl.width, 32)
        self.assertEqual(sub.status.width, 32)
        self.assertEqual(sub.data.width, 32)
        self.assertEqual(sub.wonly.width, 32)

    def test_bus_attached(self):
        sub = _make_sub()
        self.assertIsInstance(sub.bus, Axi4LiteBus)

    def test_signals_flattened(self):
        sub = _make_sub()
        sigs = sub._signals()
        self.assertIn('bus_awaddr', sigs)
        self.assertIn('bus_rdata', sigs)
        self.assertIn('ctrl', sigs)


# ── Simulation tests ─────────────────────────────────────────────────

class TestAxi4LiteSubSim(unittest.TestCase):
    def _run(self, stim_fn):
        sub = _make_sub()
        sim = SimEngine(sub)
        sim.clock(sub.clock, 10)
        results = {}
        sim.initial(lambda: stim_fn(sub, results))
        sim.run()
        return sub, results

    def test_write_and_read(self):
        def stim(s, r):
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            # Write 0xCAFEBABE to ctrl
            s.bus.awaddr.set(0x00); s.bus.awvalid.set(1)
            s.bus.wdata.set(0xCAFEBABE); s.bus.wstrb.set(0xF); s.bus.wvalid.set(1)
            yield 10
            s.bus.awvalid.set(0); s.bus.wvalid.set(0); yield 10
            r['ctrl'] = int(s.ctrl)
            # Read back
            s.bus.araddr.set(0x00); s.bus.arvalid.set(1); yield 10
            r['rdata'] = int(s.bus.rdata)
            r['rvalid'] = int(s.bus.rvalid)
            r['rresp'] = int(s.bus.rresp)

        _, r = self._run(stim)
        self.assertEqual(r['ctrl'], 0xCAFEBABE)
        self.assertEqual(r['rdata'], 0xCAFEBABE)
        self.assertEqual(r['rvalid'], 1)
        self.assertEqual(r['rresp'], RESP_OKAY)

    def test_byte_enable(self):
        def stim(s, r):
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            # Write full word
            s.bus.awaddr.set(0x00); s.bus.awvalid.set(1)
            s.bus.wdata.set(0xDEADBEEF); s.bus.wstrb.set(0xF); s.bus.wvalid.set(1)
            yield 10
            s.bus.awvalid.set(0); s.bus.wvalid.set(0); yield 10
            # Partial write: only byte 0
            s.bus.awaddr.set(0x00); s.bus.awvalid.set(1)
            s.bus.wdata.set(0x000000AA); s.bus.wstrb.set(0x1); s.bus.wvalid.set(1)
            yield 10
            s.bus.awvalid.set(0); s.bus.wvalid.set(0); yield 10
            r['ctrl'] = int(s.ctrl)

        _, r = self._run(stim)
        self.assertEqual(r['ctrl'], 0xDEADBEAA)

    def test_decerr_on_invalid_address(self):
        def stim(s, r):
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            s.bus.araddr.set(0xFF); s.bus.arvalid.set(1); yield 10
            r['rresp'] = int(s.bus.rresp)
            r['rvalid'] = int(s.bus.rvalid)

        _, r = self._run(stim)
        self.assertEqual(r['rresp'], RESP_DECERR)
        self.assertEqual(r['rvalid'], 1)

    def test_ro_register_not_writable(self):
        def stim(s, r):
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            # Set status externally (as if driven by hardware)
            s.status.set(0x42)
            yield 10
            # Try to write status via AXI
            s.bus.awaddr.set(0x04); s.bus.awvalid.set(1)
            s.bus.wdata.set(0xFF); s.bus.wstrb.set(0xF); s.bus.wvalid.set(1)
            yield 10
            s.bus.awvalid.set(0); s.bus.wvalid.set(0); yield 10
            r['status'] = int(s.status)

        _, r = self._run(stim)
        self.assertEqual(r['status'], 0x42)  # unchanged

    def test_wo_register_not_readable(self):
        def stim(s, r):
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            # Write to wo register
            s.bus.awaddr.set(0x0C); s.bus.awvalid.set(1)
            s.bus.wdata.set(0xBEEF); s.bus.wstrb.set(0xF); s.bus.wvalid.set(1)
            yield 10
            s.bus.awvalid.set(0); s.bus.wvalid.set(0); yield 10
            # Try to read it
            s.bus.araddr.set(0x0C); s.bus.arvalid.set(1); yield 10
            r['rresp'] = int(s.bus.rresp)

        _, r = self._run(stim)
        self.assertEqual(r['rresp'], RESP_DECERR)

    def test_reset_clears_rw_registers(self):
        def stim(s, r):
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            # Write
            s.bus.awaddr.set(0x00); s.bus.awvalid.set(1)
            s.bus.wdata.set(0xFF); s.bus.wstrb.set(0xF); s.bus.wvalid.set(1)
            yield 10
            s.bus.awvalid.set(0); s.bus.wvalid.set(0); yield 10
            # Reset
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            r['ctrl'] = int(s.ctrl)

        _, r = self._run(stim)
        self.assertEqual(r['ctrl'], 0)

    def test_handshake_signals(self):
        def stim(s, r):
            s.reset.set(1); yield 20
            s.reset.set(0); yield 10
            # awready follows awvalid
            s.bus.awvalid.set(1); yield 10
            r['awready_high'] = int(s.bus.awready)
            s.bus.awvalid.set(0); yield 10
            r['awready_low'] = int(s.bus.awready)

        _, r = self._run(stim)
        self.assertEqual(r['awready_high'], 1)
        self.assertEqual(r['awready_low'], 0)


# ── Verilog emission tests ───────────────────────────────────────────

class TestAxi4LiteSubVerilog(unittest.TestCase):
    def setUp(self):
        self.sub = _make_sub()
        self.v = self.sub.to_verilog()

    def test_module_declaration(self):
        self.assertIn('module axi4litesub', self.v)

    def test_bus_ports(self):
        self.assertIn('input [31:0] bus_awaddr', self.v)
        self.assertIn('output reg [31:0] bus_rdata', self.v)
        self.assertIn('input [3:0] bus_wstrb', self.v)

    def test_register_declarations(self):
        self.assertIn('reg [31:0] ctrl', self.v)
        self.assertIn('reg [31:0] status', self.v)

    def test_address_decode(self):
        # Read decode should have case entries
        self.assertIn('case (bus_araddr)', self.v)

    def test_write_decode(self):
        self.assertIn('case (bus_awaddr)', self.v)

    def test_iverilog_compiles(self):
        with tempfile.NamedTemporaryFile(suffix='.v', mode='w', delete=False) as f:
            f.write(self.v)
            f.flush()
            r = subprocess.run(['iverilog', '-o', '/dev/null', f.name],
                               capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual(r.returncode, 0, f'iverilog failed:\n{r.stderr}')


if __name__ == '__main__':
    unittest.main()
