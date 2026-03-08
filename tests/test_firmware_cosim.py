"""Tests for firmware co-simulation: ELF loader, FlatMemory, SocSim, UartPeripheral."""

import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy.firmware import ElfImage, ElfSegment, PF_R, PF_W, PF_X, load_elf
from veripy.soc import parse_soc_config
from veripy.soc_sim import FlatMemory, SocSim, UartPeripheral


# ── ELF builder helpers ───────────────────────────────────────────────────────

def _make_elf32(entry: int, segments: list[tuple[int, bytes]]) -> bytes:
    """Build a minimal ELF32 LE binary with PT_LOAD segments."""
    e_ident = (
        b'\x7fELF'   # magic
        + bytes([1])  # ELFCLASS32
        + bytes([1])  # ELFDATA2LSB
        + bytes([1])  # EV_CURRENT
        + bytes(9)    # padding
    )
    e_phnum = len(segments)
    e_ehsize = 52
    e_phentsize = 32
    e_phoff = e_ehsize  # program headers immediately after ELF header

    # Lay out segment data after all program headers
    data_offset = e_ehsize + e_phnum * e_phentsize
    seg_offsets = []
    seg_data_blobs = []
    cur = data_offset
    for _, data in segments:
        seg_offsets.append(cur)
        seg_data_blobs.append(data)
        cur += len(data)

    hdr = e_ident + struct.pack(
        '<HHIIIIIHHHHHH',
        2,           # ET_EXEC
        0xF3,        # EM_RISCV
        1,           # EV_CURRENT
        entry,       # e_entry
        e_phoff,     # e_phoff
        0,           # e_shoff
        0,           # e_flags
        e_ehsize,    # e_ehsize
        e_phentsize, # e_phentsize
        e_phnum,     # e_phnum
        0, 0, 0,     # shentsize, shnum, shstrndx
    )

    phdrs = b''
    for (vaddr, data), off in zip(segments, seg_offsets):
        phdrs += struct.pack(
            '<IIIIIIII',
            1,           # PT_LOAD
            off,         # p_offset
            vaddr,       # p_vaddr
            vaddr,       # p_paddr
            len(data),   # p_filesz
            len(data),   # p_memsz
            PF_R | PF_X, # p_flags
            4,           # p_align
        )

    return hdr + phdrs + b''.join(seg_data_blobs)


def _make_elf32_bss(entry: int, vaddr: int, data: bytes, bss_extra: int) -> bytes:
    """ELF32 with one segment where memsz > filesz (BSS)."""
    e_ident = (
        b'\x7fELF' + bytes([1, 1, 1]) + bytes(9)
    )
    e_ehsize = 52
    e_phentsize = 32
    e_phoff = e_ehsize
    data_offset = e_ehsize + e_phentsize

    hdr = e_ident + struct.pack(
        '<HHIIIIIHHHHHH',
        2, 0xF3, 1, entry, e_phoff, 0, 0,
        e_ehsize, e_phentsize, 1, 0, 0, 0,
    )
    phdr = struct.pack(
        '<IIIIIIII',
        1, data_offset, vaddr, vaddr,
        len(data), len(data) + bss_extra,
        PF_R | PF_W | PF_X, 4,
    )
    return hdr + phdr + data


def _write_tmp(data: bytes) -> str:
    f = tempfile.NamedTemporaryFile(suffix='.elf', delete=False)
    f.write(data)
    f.close()
    return f.name


def _soc_config(uart_base: int = 0x40000000) -> object:
    d = {
        'soc': {'name': 'test'},
        'memory': [
            {'name': 'rom', 'base': '0x00000000', 'size': '0x10000', 'type': 'rom'},
            {'name': 'ram', 'base': '0x20000000', 'size': '0x8000',  'type': 'ram'},
        ],
        'peripherals': [
            {'name': 'uart0', 'base': hex(uart_base), 'size': '0x100', 'type': 'uart'},
        ],
        'platform': {'clock': 'clk', 'reset': 'rst', 'reset_active': 'high'},
    }
    with tempfile.NamedTemporaryFile(suffix='.json', mode='w', delete=False) as f:
        json.dump(d, f)
        return f.name


# ── load_elf ──────────────────────────────────────────────────────────────────

class TestLoadElf(unittest.TestCase):

    def test_single_segment(self):
        payload = b'\x01\x02\x03\x04'
        raw = _make_elf32(entry=0x1000, segments=[(0x0000, payload)])
        path = _write_tmp(raw)
        try:
            img = load_elf(path)
        finally:
            os.unlink(path)
        self.assertEqual(img.entry, 0x1000)
        self.assertEqual(img.bits, 32)
        self.assertEqual(len(img.segments), 1)
        self.assertEqual(img.segments[0].vaddr, 0x0000)
        self.assertEqual(img.segments[0].data, payload)
        self.assertEqual(img.segments[0].memsz, len(payload))

    def test_two_segments(self):
        raw = _make_elf32(entry=0x2000, segments=[
            (0x0000, b'\xAA' * 16),
            (0x1000, b'\xBB' * 8),
        ])
        path = _write_tmp(raw)
        try:
            img = load_elf(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(img.segments), 2)
        self.assertEqual(img.segments[0].vaddr, 0x0000)
        self.assertEqual(img.segments[1].vaddr, 0x1000)

    def test_bss_memsz(self):
        raw = _make_elf32_bss(entry=0x0, vaddr=0x0, data=b'\x11' * 4, bss_extra=8)
        path = _write_tmp(raw)
        try:
            img = load_elf(path)
        finally:
            os.unlink(path)
        seg = img.segments[0]
        self.assertEqual(len(seg.data), 4)
        self.assertEqual(seg.memsz, 12)

    def test_bad_magic(self):
        path = _write_tmp(b'\x00' * 64)
        try:
            with self.assertRaises(ValueError):
                load_elf(path)
        finally:
            os.unlink(path)

    def test_big_endian_rejected(self):
        raw = bytearray(_make_elf32(0, [(0, b'\x00')]))
        raw[5] = 2  # ELFDATA2MSB
        path = _write_tmp(bytes(raw))
        try:
            with self.assertRaises(ValueError):
                load_elf(path)
        finally:
            os.unlink(path)


# ── FlatMemory ────────────────────────────────────────────────────────────────

class TestFlatMemory(unittest.TestCase):

    def _mem(self):
        m = FlatMemory()
        m.add_region('rom', 0x0000, 0x1000)
        m.add_region('ram', 0x2000, 0x1000)
        return m

    def test_write_read_32(self):
        m = self._mem()
        m.write(0x0000, 0xDEADBEEF, 4)
        self.assertEqual(m.read(0x0000, 4), 0xDEADBEEF)

    def test_write_read_8(self):
        m = self._mem()
        m.write(0x2000, 0xAB, 1)
        self.assertEqual(m.read(0x2000, 1), 0xAB)

    def test_unmapped_returns_zero(self):
        m = self._mem()
        self.assertEqual(m.read(0x9000, 4), 0)

    def test_unmapped_write_noop(self):
        m = self._mem()
        m.write(0x9000, 0xFF, 1)  # should not raise

    def test_load_bytes(self):
        m = self._mem()
        m.load_bytes(0x0004, b'\x01\x02\x03\x04')
        self.assertEqual(m.read(0x0004, 1), 0x01)
        self.assertEqual(m.read(0x0007, 1), 0x04)

    def test_little_endian(self):
        m = self._mem()
        m.write(0x0000, 0x01020304, 4)
        self.assertEqual(m.read(0x0000, 1), 0x04)
        self.assertEqual(m.read(0x0001, 1), 0x03)
        self.assertEqual(m.read(0x0002, 1), 0x02)
        self.assertEqual(m.read(0x0003, 1), 0x01)

    def test_two_regions_independent(self):
        m = self._mem()
        m.write(0x0000, 0xAA, 1)
        m.write(0x2000, 0xBB, 1)
        self.assertEqual(m.read(0x0000, 1), 0xAA)
        self.assertEqual(m.read(0x2000, 1), 0xBB)


# ── UartPeripheral ────────────────────────────────────────────────────────────

class TestUartPeripheral(unittest.TestCase):

    def test_tx_capture(self):
        u = UartPeripheral(base=0x40000000)
        u.write(0x40000000, ord('H'), 1)
        u.write(0x40000000, ord('i'), 1)
        self.assertEqual(u.output, 'Hi')

    def test_non_tx_write_ignored(self):
        u = UartPeripheral(base=0x40000000)
        u.write(0x40000004, ord('X'), 1)  # offset 4, not TX
        self.assertEqual(u.output, '')

    def test_read_returns_zero(self):
        u = UartPeripheral(base=0x40000000)
        self.assertEqual(u.read(0x40000000, 1), 0)


# ── SocSim ────────────────────────────────────────────────────────────────────

class TestSocSim(unittest.TestCase):

    def setUp(self):
        self._cfg_path = _soc_config()
        self._config = parse_soc_config(self._cfg_path)

    def tearDown(self):
        os.unlink(self._cfg_path)

    def test_memory_regions_created(self):
        sim = SocSim(self._config)
        # ROM region: write and read back
        sim.memory.write(0x0000, 0x12345678, 4)
        self.assertEqual(sim.memory.read(0x0000, 4), 0x12345678)
        # RAM region
        sim.memory.write(0x20000000, 0xABCD, 2)
        self.assertEqual(sim.memory.read(0x20000000, 2), 0xABCD)

    def test_uart_peripheral_created(self):
        sim = SocSim(self._config)
        self.assertIsNotNone(sim.uart)

    def test_write_dispatch_to_uart(self):
        sim = SocSim(self._config)
        sim.write(0x40000000, ord('A'), 1)
        self.assertEqual(sim.uart.output, 'A')

    def test_read_dispatch_to_uart(self):
        sim = SocSim(self._config)
        self.assertEqual(sim.read(0x40000000, 1), 0)

    def test_write_dispatch_to_memory(self):
        sim = SocSim(self._config)
        sim.write(0x00000000, 0xCAFE, 2)
        self.assertEqual(sim.read(0x00000000, 2), 0xCAFE)

    def test_load_elf(self):
        payload = b'\x93\x00\x00\x00' * 4  # NOP instructions
        raw = _make_elf32(entry=0x00000000, segments=[(0x00000000, payload)])
        elf_path = _write_tmp(raw)
        try:
            sim = SocSim(self._config)
            entry = sim.load_elf(elf_path)
        finally:
            os.unlink(elf_path)
        self.assertEqual(entry, 0x00000000)
        self.assertEqual(sim.memory.read(0x00000000, 4), 0x00000093)

    def test_load_elf_bss_zeroed(self):
        # Segment with BSS: data=4 bytes, memsz=8 bytes
        raw = _make_elf32_bss(entry=0x20000000, vaddr=0x20000000,
                               data=b'\xFF\xFF\xFF\xFF', bss_extra=4)
        elf_path = _write_tmp(raw)
        try:
            sim = SocSim(self._config)
            # Pre-dirty the RAM
            sim.memory.write(0x20000004, 0xDEAD, 2)
            sim.load_elf(elf_path)
        finally:
            os.unlink(elf_path)
        # BSS region should be zeroed
        self.assertEqual(sim.memory.read(0x20000004, 4), 0x00000000)

    def test_no_uart_config(self):
        d = {
            'soc': {'name': 'bare'},
            'memory': [{'name': 'rom', 'base': '0x0', 'size': '0x1000', 'type': 'rom'}],
            'platform': {'clock': 'clk', 'reset': 'rst', 'reset_active': 'high'},
        }
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', delete=False) as f:
            json.dump(d, f)
            path = f.name
        try:
            config = parse_soc_config(path)
            sim = SocSim(config)
        finally:
            os.unlink(path)
        self.assertIsNone(sim.uart)


if __name__ == '__main__':
    unittest.main()
