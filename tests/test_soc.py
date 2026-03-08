"""Tests for the SoC builder (veripy/soc.py)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy.soc import (
    AddressMap, AddrEntry, BusConfig, CpuConfig, MemoryRegion,
    Peripheral, PlatformConfig, SocConfig,
    build_interconnect, build_top, gen_c_header, gen_doc, gen_linker_script,
    parse_soc_config,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_json(d, path):
    with open(path, 'w') as f:
        json.dump(d, f)


def _minimal_config_dict(name='test_soc'):
    return {
        'soc': {'name': name},
        'memory': [
            {'name': 'rom', 'base': '0x00000000', 'size': '0x10000', 'type': 'rom'},
            {'name': 'ram', 'base': '0x20000000', 'size': '0x8000',  'type': 'ram'},
        ],
        'platform': {'clock': 'clk', 'reset': 'rst', 'reset_active': 'high'},
    }


def _make_config(extra=None):
    d = _minimal_config_dict()
    if extra:
        d.update(extra)
    return d


def _parse_json_config(d):
    with tempfile.NamedTemporaryFile(suffix='.json', mode='w', delete=False) as f:
        json.dump(d, f)
        path = f.name
    try:
        return parse_soc_config(path)
    finally:
        os.unlink(path)


# ── parse_soc_config ──────────────────────────────────────────────────────────

class TestParseSocConfig(unittest.TestCase):

    def test_minimal(self):
        cfg = _parse_json_config(_minimal_config_dict())
        self.assertEqual(cfg.name, 'test_soc')
        self.assertEqual(len(cfg.memory), 2)
        self.assertIsNone(cfg.cpu)
        self.assertEqual(cfg.bus.data_width, 32)
        self.assertEqual(cfg.platform.clock, 'clk')

    def test_hex_base_and_size(self):
        cfg = _parse_json_config(_minimal_config_dict())
        self.assertEqual(cfg.memory[0].base, 0x00000000)
        self.assertEqual(cfg.memory[0].size, 0x10000)

    def test_int_base_and_size(self):
        d = _minimal_config_dict()
        d['memory'][0]['base'] = 0
        d['memory'][0]['size'] = 65536
        cfg = _parse_json_config(d)
        self.assertEqual(cfg.memory[0].base, 0)
        self.assertEqual(cfg.memory[0].size, 65536)

    def test_missing_soc_name(self):
        d = _minimal_config_dict()
        d['soc'] = {}
        with self.assertRaises(ValueError) as ctx:
            _parse_json_config(d)
        self.assertIn('soc.name', str(ctx.exception))

    def test_missing_memory(self):
        d = _minimal_config_dict()
        d['memory'] = []
        with self.assertRaises(ValueError):
            _parse_json_config(d)

    def test_missing_platform_clock(self):
        d = _minimal_config_dict()
        d['platform'] = {'reset': 'rst'}
        with self.assertRaises(ValueError) as ctx:
            _parse_json_config(d)
        self.assertIn('platform.clock', str(ctx.exception))

    def test_invalid_reset_active(self):
        d = _minimal_config_dict()
        d['platform']['reset_active'] = 'medium'
        with self.assertRaises(ValueError):
            _parse_json_config(d)

    def test_cpu_section(self):
        d = _minimal_config_dict()
        d['cpu'] = {'type': 'blackbox', 'clock': 'clk', 'reset': 'rst', 'bus_prefix': 'dbus'}
        cfg = _parse_json_config(d)
        self.assertIsNotNone(cfg.cpu)
        self.assertEqual(cfg.cpu.bus_prefix, 'dbus')

    def test_peripherals(self):
        d = _minimal_config_dict()
        d['peripherals'] = [{'name': 'uart0', 'base': '0x40000000', 'size': '0x100'}]
        cfg = _parse_json_config(d)
        self.assertEqual(len(cfg.peripherals), 1)
        self.assertEqual(cfg.peripherals[0].name, 'uart0')
        self.assertEqual(cfg.peripherals[0].base, 0x40000000)

    def test_bus_section(self):
        d = _minimal_config_dict()
        d['bus'] = {'data_width': 64, 'addr_width': 64}
        cfg = _parse_json_config(d)
        self.assertEqual(cfg.bus.data_width, 64)

    def test_invalid_memory_type(self):
        d = _minimal_config_dict()
        d['memory'][0]['type'] = 'nvme'
        with self.assertRaises(ValueError):
            _parse_json_config(d)

    def test_memory_missing_base(self):
        d = _minimal_config_dict()
        del d['memory'][0]['base']
        with self.assertRaises(ValueError):
            _parse_json_config(d)


# ── AddressMap ────────────────────────────────────────────────────────────────

class TestAddressMap(unittest.TestCase):

    def _make(self, extra_periph=None):
        d = _minimal_config_dict()
        if extra_periph:
            d['peripherals'] = extra_periph
        return _parse_json_config(d)

    def test_sorted_by_base(self):
        cfg = self._make()
        am = AddressMap(cfg)
        bases = [e.base for e in am]
        self.assertEqual(bases, sorted(bases))

    def test_overlap_detection(self):
        d = _minimal_config_dict()
        # ROM ends at 0x10000, second region starts at 0x8000 → overlap
        d['memory'].append({'name': 'overlap', 'base': '0x8000', 'size': '0x1000', 'type': 'ram'})
        cfg = _parse_json_config(d)
        with self.assertRaises(ValueError) as ctx:
            AddressMap(cfg)
        self.assertIn('overlap', str(ctx.exception).lower())

    def test_no_overlap_adjacent(self):
        d = _minimal_config_dict()
        # ROM ends at 0x10000, next starts at 0x10000 → no overlap
        d['memory'].append({'name': 'extra', 'base': '0x10000', 'size': '0x1000', 'type': 'ram'})
        cfg = _parse_json_config(d)
        am = AddressMap(cfg)
        self.assertEqual(len(am), 3)

    def test_peripheral_overlap_with_memory(self):
        d = _minimal_config_dict()
        d['peripherals'] = [{'name': 'bad', 'base': '0x00001000', 'size': '0x100'}]
        cfg = _parse_json_config(d)
        with self.assertRaises(ValueError):
            AddressMap(cfg)

    def test_entry_kinds(self):
        d = _minimal_config_dict()
        d['peripherals'] = [{'name': 'uart0', 'base': '0x40000000', 'size': '0x100'}]
        cfg = _parse_json_config(d)
        am = AddressMap(cfg)
        kinds = {e.name: e.kind for e in am}
        self.assertEqual(kinds['rom'], 'memory')
        self.assertEqual(kinds['ram'], 'memory')
        self.assertEqual(kinds['uart0'], 'peripheral')


# ── gen_c_header ──────────────────────────────────────────────────────────────

class TestGenCHeader(unittest.TestCase):

    def setUp(self):
        d = _minimal_config_dict()
        d['peripherals'] = [{'name': 'uart0', 'base': '0x40000000', 'size': '0x100'}]
        self.cfg = _parse_json_config(d)
        self.am = AddressMap(self.cfg)
        self.hdr = gen_c_header(self.cfg, self.am)

    def test_include_guard(self):
        self.assertIn('#ifndef _TEST_SOC_SOC_H', self.hdr)
        self.assertIn('#define _TEST_SOC_SOC_H', self.hdr)
        self.assertIn('#endif', self.hdr)

    def test_base_macros(self):
        self.assertIn('#define ROM_BASE  0x00000000UL', self.hdr)
        self.assertIn('#define RAM_BASE  0x20000000UL', self.hdr)
        self.assertIn('#define UART0_BASE  0x40000000UL', self.hdr)

    def test_size_macros(self):
        self.assertIn('#define ROM_SIZE  0x00010000UL', self.hdr)
        self.assertIn('#define RAM_SIZE  0x00008000UL', self.hdr)

    def test_end_macros(self):
        self.assertIn('#define ROM_END   0x00010000UL', self.hdr)


# ── gen_linker_script ─────────────────────────────────────────────────────────

class TestGenLinkerScript(unittest.TestCase):

    def setUp(self):
        self.cfg = _parse_json_config(_minimal_config_dict())
        self.am = AddressMap(self.cfg)
        self.ld = gen_linker_script(self.cfg, self.am)

    def test_memory_block(self):
        self.assertIn('MEMORY', self.ld)
        self.assertIn('ROM', self.ld)
        self.assertIn('RAM', self.ld)
        self.assertIn('ORIGIN = 0x00000000', self.ld)
        self.assertIn('LENGTH = 0x00010000', self.ld)

    def test_sections_block(self):
        self.assertIn('SECTIONS', self.ld)
        self.assertIn('.text', self.ld)
        self.assertIn('.bss', self.ld)
        self.assertIn('.data', self.ld)

    def test_rom_text_placement(self):
        self.assertIn('> ROM', self.ld)

    def test_ram_data_placement(self):
        self.assertIn('> RAM', self.ld)

    def test_peripherals_not_in_memory_block(self):
        d = _minimal_config_dict()
        d['peripherals'] = [{'name': 'uart0', 'base': '0x40000000', 'size': '0x100'}]
        cfg = _parse_json_config(d)
        am = AddressMap(cfg)
        ld = gen_linker_script(cfg, am)
        self.assertNotIn('UART0', ld)


# ── build_interconnect ────────────────────────────────────────────────────────

class TestBuildInterconnect(unittest.TestCase):

    def setUp(self):
        d = _minimal_config_dict()
        d['cpu'] = {'type': 'blackbox', 'clock': 'clk', 'reset': 'rst', 'bus_prefix': 'dbus'}
        d['peripherals'] = [{'name': 'uart0', 'base': '0x40000000', 'size': '0x100'}]
        self.cfg = _parse_json_config(d)
        self.am = AddressMap(self.cfg)
        self.v = build_interconnect(self.cfg, self.am)

    def test_module_name(self):
        self.assertIn('module test_soc_interconnect', self.v)

    def test_endmodule(self):
        self.assertIn('endmodule', self.v)

    def test_master_ports(self):
        self.assertIn('dbus_awaddr', self.v)
        self.assertIn('dbus_rdata', self.v)

    def test_subordinate_ports(self):
        self.assertIn('rom_awaddr', self.v)
        self.assertIn('uart0_rdata', self.v)

    def test_address_decode(self):
        self.assertIn('wr_sel', self.v)
        self.assertIn('rd_sel', self.v)
        self.assertIn("32'h00000000", self.v)
        self.assertIn("32'h40000000", self.v)

    def test_no_cpu_no_decode(self):
        d = _minimal_config_dict()
        cfg = _parse_json_config(d)
        am = AddressMap(cfg)
        v = build_interconnect(cfg, am)
        self.assertNotIn('wr_sel', v)
        self.assertIn('endmodule', v)


# ── build_top ─────────────────────────────────────────────────────────────────

class TestBuildTop(unittest.TestCase):

    def setUp(self):
        d = _minimal_config_dict()
        d['cpu'] = {'type': 'blackbox', 'clock': 'clk', 'reset': 'rst', 'bus_prefix': 'dbus'}
        self.cfg = _parse_json_config(d)
        self.am = AddressMap(self.cfg)
        self.v = build_top(self.cfg, self.am)

    def test_module_name(self):
        self.assertIn('module test_soc', self.v)

    def test_endmodule(self):
        self.assertIn('endmodule', self.v)

    def test_wire_declarations(self):
        self.assertIn('wire', self.v)
        self.assertIn('rom_awaddr', self.v)
        self.assertIn('dbus_awaddr', self.v)

    def test_interconnect_instantiation(self):
        self.assertIn('test_soc_interconnect', self.v)

    def test_memory_instantiation(self):
        self.assertIn('rom_inst', self.v)
        self.assertIn('ram_inst', self.v)


# ── gen_doc ───────────────────────────────────────────────────────────────────

class TestGenDoc(unittest.TestCase):

    def _make(self, with_cpu=False, with_periph=False):
        d = _minimal_config_dict()
        if with_cpu:
            d['cpu'] = {'type': 'blackbox', 'clock': 'clk', 'reset': 'rst', 'bus_prefix': 'dbus'}
        if with_periph:
            d.setdefault('peripherals', []).append(
                {'name': 'uart0', 'base': '0x40000000', 'size': '0x100'}
            )
        cfg = _parse_json_config(d)
        am = AddressMap(cfg)
        return gen_doc(cfg, am)

    def test_title(self):
        doc = self._make()
        self.assertIn('# test_soc SoC', doc)

    def test_memory_map_table_header(self):
        doc = self._make()
        self.assertIn('## Memory Map', doc)
        self.assertIn('| Name |', doc)
        self.assertIn('| Base |', doc)

    def test_memory_map_entries(self):
        doc = self._make()
        self.assertIn('rom', doc)
        self.assertIn('0x00000000', doc)
        self.assertIn('ram', doc)
        self.assertIn('0x20000000', doc)

    def test_no_cpu_section_when_absent(self):
        doc = self._make(with_cpu=False)
        self.assertNotIn('## CPU', doc)

    def test_cpu_section(self):
        doc = self._make(with_cpu=True)
        self.assertIn('## CPU', doc)
        self.assertIn('dbus', doc)

    def test_no_peripherals_section_when_absent(self):
        doc = self._make(with_periph=False)
        self.assertNotIn('## Peripherals', doc)

    def test_peripherals_section(self):
        doc = self._make(with_periph=True)
        self.assertIn('## Peripherals', doc)
        self.assertIn('uart0', doc)
        self.assertIn('0x40000000', doc)

    def test_block_diagram(self):
        doc = self._make()
        self.assertIn('## Block Diagram', doc)
        self.assertIn('```', doc)
        self.assertIn('test_soc', doc)

    def test_peripheral_file_shown(self):
        d = _minimal_config_dict()
        d['peripherals'] = [{'name': 'uart0', 'base': '0x40000000', 'size': '0x100', 'file': 'uart.v'}]
        cfg = _parse_json_config(d)
        am = AddressMap(cfg)
        doc = gen_doc(cfg, am)
        self.assertIn('uart.v', doc)


# ── build_soc (integration) ───────────────────────────────────────────────────

class TestBuildSoc(unittest.TestCase):

    def test_outputs_created(self):
        d = _minimal_config_dict()
        d['cpu'] = {'type': 'blackbox', 'clock': 'clk', 'reset': 'rst', 'bus_prefix': 'dbus'}
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, 'soc.json')
            _write_json(d, cfg_path)
            out_dir = os.path.join(tmpdir, 'out')
            from veripy.soc import build_soc
            build_soc(cfg_path, out_dir)
            files = os.listdir(out_dir)
            self.assertIn('test_soc_interconnect.v', files)
            self.assertIn('test_soc_top.v', files)
            self.assertIn('test_soc.h', files)
            self.assertIn('test_soc.ld', files)
            self.assertIn('test_soc.md', files)


if __name__ == '__main__':
    unittest.main()
