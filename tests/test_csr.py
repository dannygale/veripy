"""Tests for CSR register map generator."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
import tempfile
import subprocess
from veripy.csr import Field, Reg, RegisterMap
from veripy import Module
from veripy.verify import TestBench, initial


def _make_rmap():
    return RegisterMap([
        Reg('ctrl', 0x00, [
            Field('enable', bits=0, access='rw', reset=1),
            Field('mode',   bits=(2, 1), access='rw', reset=0),
        ]),
        Reg('status', 0x04, [
            Field('busy',  bits=0, access='ro'),
            Field('error', bits=1, access='ro'),
        ]),
        Reg('data', 0x08, [
            Field('value', bits=(31, 0), access='rw'),
        ]),
    ])


# ── Field tests ──────────────────────────────────────────────────────

class TestField(unittest.TestCase):
    def test_single_bit(self):
        f = Field('en', bits=0)
        self.assertEqual(f.msb, 0)
        self.assertEqual(f.lsb, 0)
        self.assertEqual(f.width, 1)
        self.assertEqual(f.mask, 0x1)

    def test_range(self):
        f = Field('mode', bits=(7, 4))
        self.assertEqual(f.width, 4)
        self.assertEqual(f.mask, 0xF0)

    def test_msb_lt_lsb_raises(self):
        with self.assertRaises(ValueError):
            Field('bad', bits=(0, 7))


# ── Reg tests ────────────────────────────────────────────────────────

class TestReg(unittest.TestCase):
    def test_access_all_rw(self):
        r = Reg('r', 0, [Field('a', 0, access='rw')])
        self.assertEqual(r.access, 'rw')

    def test_access_all_ro(self):
        r = Reg('r', 0, [Field('a', 0, access='ro')])
        self.assertEqual(r.access, 'ro')

    def test_access_mixed(self):
        r = Reg('r', 0, [Field('a', 0, access='ro'), Field('b', 1, access='rw')])
        self.assertEqual(r.access, 'rw')

    def test_reset_val(self):
        r = Reg('r', 0, [
            Field('a', bits=0, reset=1),
            Field('b', bits=(3, 1), reset=0b101),
        ])
        self.assertEqual(r.reset_val, 0b1011)

    def test_overlap_raises(self):
        with self.assertRaises(ValueError):
            Reg('r', 0, [Field('a', bits=(1, 0)), Field('b', bits=1)])


# ── RegisterMap validation ───────────────────────────────────────────

class TestRegisterMapValidation(unittest.TestCase):
    def test_duplicate_offset_raises(self):
        with self.assertRaises(ValueError):
            RegisterMap([Reg('a', 0x00), Reg('b', 0x00)])


# ── to_module() ──────────────────────────────────────────────────────

class TestToModule(unittest.TestCase):
    def test_returns_module(self):
        mod = _make_rmap().to_module()
        self.assertIsInstance(mod, Module)

    def test_registers_exist(self):
        mod = _make_rmap().to_module()
        self.assertEqual(mod.ctrl.width, 32)
        self.assertEqual(mod.status.width, 32)
        self.assertEqual(mod.data.width, 32)

    def test_verilog_compiles(self):
        mod = _make_rmap().to_module()
        v = mod.to_verilog()
        with tempfile.NamedTemporaryFile(suffix='.v', mode='w', delete=False) as f:
            f.write(v)
            f.flush()
            r = subprocess.run(['iverilog', '-o', '/dev/null', f.name],
                               capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual(r.returncode, 0, f'iverilog failed:\n{r.stderr}')


# ── Simulation via to_module() ───────────────────────────────────────

class TestToModuleSim(TestBench):
    def create_module(self): return _make_rmap().to_module()

    def test_write_read_roundtrip(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awaddr = 0x00; dut.bus_awvalid = 1
            dut.bus_wdata = 0xAB; dut.bus_wstrb = 0xF; dut.bus_wvalid = 1
            yield 10; dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            dut.bus_araddr = 0x00; dut.bus_arvalid = 1; yield 10
            self.assertEqual(self.get('bus_rdata'), 0xAB)
            self.assertEqual(self.get('bus_rvalid'), 1)

    def test_ro_not_writable(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.reset = 1; yield 20; dut.reset = 0; yield 10
            dut.bus_awaddr = 0x04; dut.bus_awvalid = 1
            dut.bus_wdata = 0xFF; dut.bus_wstrb = 0xF; dut.bus_wvalid = 1
            yield 10; dut.bus_awvalid = 0; dut.bus_wvalid = 0; yield 10
            self.assertEqual(self.get('status'), 0)


# ── to_c_header() ───────────────────────────────────────────────────

class TestToCHeader(unittest.TestCase):
    def setUp(self):
        self.hdr = _make_rmap().to_c_header('MY_IP')

    def test_include_guard(self):
        self.assertIn('#ifndef _MY_IP_REGS_H', self.hdr)
        self.assertIn('#endif', self.hdr)

    def test_offsets(self):
        self.assertIn('#define MY_IP_CTRL_OFFSET 0x0000', self.hdr)
        self.assertIn('#define MY_IP_STATUS_OFFSET 0x0004', self.hdr)
        self.assertIn('#define MY_IP_DATA_OFFSET 0x0008', self.hdr)

    def test_reset_values(self):
        self.assertIn('#define MY_IP_CTRL_RESET  0x00000001', self.hdr)

    def test_field_masks(self):
        self.assertIn('#define MY_IP_CTRL_ENABLE_SHIFT 0', self.hdr)
        self.assertIn('#define MY_IP_CTRL_ENABLE_MASK  0x00000001', self.hdr)
        self.assertIn('#define MY_IP_CTRL_MODE_SHIFT 1', self.hdr)
        self.assertIn('#define MY_IP_CTRL_MODE_MASK  0x00000006', self.hdr)


# ── to_python_driver() ──────────────────────────────────────────────

class TestToPythonDriver(unittest.TestCase):
    def setUp(self):
        self.src = _make_rmap().to_python_driver('TestDrv')

    def test_class_name(self):
        self.assertIn('class TestDrv:', self.src)

    def test_default_class_name(self):
        src = _make_rmap().to_python_driver()
        self.assertIn('class RegDriver:', src)

    def test_offset_constants(self):
        self.assertIn('CTRL_OFFSET = 0x0000', self.src)
        self.assertIn('STATUS_OFFSET = 0x0004', self.src)
        self.assertIn('DATA_OFFSET = 0x0008', self.src)

    def test_field_constants(self):
        self.assertIn('CTRL_ENABLE_SHIFT = 0', self.src)
        self.assertIn('CTRL_ENABLE_MASK = 0x00000001', self.src)
        self.assertIn('CTRL_MODE_SHIFT = 1', self.src)
        self.assertIn('CTRL_MODE_MASK = 0x00000006', self.src)

    def test_rw_reg_has_read_and_write(self):
        self.assertIn('def read_ctrl(self):', self.src)
        self.assertIn('def write_ctrl(self, val):', self.src)

    def test_ro_reg_has_read_only(self):
        self.assertIn('def read_status(self):', self.src)
        self.assertNotIn('def write_status(', self.src)

    def test_ro_field_has_get_only(self):
        self.assertIn('def get_status_busy(self):', self.src)
        self.assertNotIn('def set_status_busy(', self.src)

    def test_rw_field_has_get_and_set(self):
        self.assertIn('def get_ctrl_enable(self):', self.src)
        self.assertIn('def set_ctrl_enable(self, val):', self.src)

    def test_generated_code_executes(self):
        """Compile the generated driver and exercise read/write via a mock bus."""
        ns = {}
        exec(self.src, ns)
        Drv = ns['TestDrv']

        class MockBus:
            def __init__(self):
                self.mem = {}
            def read(self, addr):
                return self.mem.get(addr, 0)
            def write(self, addr, data):
                self.mem[addr] = data

        bus = MockBus()
        drv = Drv(bus)
        drv.write_ctrl(0xFF)
        self.assertEqual(drv.read_ctrl(), 0xFF)
        self.assertEqual(drv.get_ctrl_enable(), 1)
        self.assertEqual(drv.get_ctrl_mode(), 3)
        # field-level set (read-modify-write)
        drv.set_ctrl_mode(0)
        self.assertEqual(drv.get_ctrl_mode(), 0)
        self.assertEqual(drv.get_ctrl_enable(), 1)  # untouched

    def test_wo_reg(self):
        """A write-only register should have write but no read."""
        rmap = RegisterMap([
            Reg('txdata', 0x00, [Field('payload', bits=(7, 0), access='wo')]),
        ])
        src = rmap.to_python_driver()
        self.assertIn('def write_txdata(self, val):', src)
        self.assertNotIn('def read_txdata(', src)
        self.assertNotIn('def get_txdata_', src)
        self.assertIn('def set_txdata_payload(self, val):', src)


# ── to_markdown() ───────────────────────────────────────────────────

class TestToMarkdown(unittest.TestCase):
    def setUp(self):
        self.md = _make_rmap().to_markdown('Test Map')

    def test_title(self):
        self.assertTrue(self.md.startswith('# Test Map'))

    def test_default_title(self):
        md = _make_rmap().to_markdown()
        self.assertIn('# Register Map', md)

    def test_register_headings(self):
        self.assertIn('## ctrl (0x0000)', self.md)
        self.assertIn('## status (0x0004)', self.md)
        self.assertIn('## data (0x0008)', self.md)

    def test_access_and_reset(self):
        self.assertIn('Access: rw | Reset: 0x00000001', self.md)
        self.assertIn('Access: ro | Reset: 0x00000000', self.md)

    def test_field_table_header(self):
        self.assertIn('| Bits | Field | Access | Reset | Description |', self.md)

    def test_field_rows(self):
        self.assertIn('| 0 | enable | rw | 0x1 |', self.md)
        self.assertIn('| 2:1 | mode | rw | 0x0 |', self.md)
        self.assertIn('| 0 | busy | ro | 0x0 |', self.md)

    def test_description_included(self):
        rmap = RegisterMap([
            Reg('ctrl', 0x00, [Field('en', 0, desc='Enable bit')], desc='Main control'),
        ])
        md = rmap.to_markdown()
        self.assertIn('Main control', md)
        self.assertIn('Enable bit', md)


if __name__ == '__main__':
    unittest.main()
