"""Tests for memory inference annotations (ram_style)."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, Mem


class _RamModule(Module):
    """Minimal module with a styled Mem for testing."""
    def __init__(self, style=None):
        self.clock = Input()
        self.reset = Input()
        self.addr = Input(4)
        self.wdata = Input(8)
        self.wen = Input()
        self.rdata = Output(8)
        self.ram = Mem(16, 8, style=style)
        super().__init__()

        @self.comb
        def read():
            self.rdata = self.ram[self.addr]

        @self.posedge(self.clock)
        def write():
            if self.wen:
                self.ram.write(self.addr, self.wdata)


class TestMemStyle(unittest.TestCase):
    def test_default_no_style(self):
        m = Mem(16, 8)
        self.assertIsNone(m.style)

    def test_block_style(self):
        m = Mem(16, 8, style='block')
        self.assertEqual(m.style, 'block')

    def test_distributed_style(self):
        m = Mem(16, 8, style='distributed')
        self.assertEqual(m.style, 'distributed')

    def test_ultra_style(self):
        m = Mem(16, 8, style='ultra')
        self.assertEqual(m.style, 'ultra')

    def test_invalid_style_raises(self):
        with self.assertRaises(ValueError):
            Mem(16, 8, style='invalid')

    def test_clone_preserves_style(self):
        m = Mem(16, 8, style='block')
        m.name = 'ram'
        c = m._clone({})
        self.assertEqual(c.style, 'block')


class TestMemStyleVerilog(unittest.TestCase):
    def test_no_style_no_attribute(self):
        v = _RamModule().to_verilog()
        self.assertNotIn('ram_style', v)
        self.assertIn('reg [7:0] ram [0:15];', v)

    def test_block_attribute(self):
        v = _RamModule(style='block').to_verilog()
        self.assertIn('(* ram_style = "block" *) reg [7:0] ram [0:15];', v)

    def test_distributed_attribute(self):
        v = _RamModule(style='distributed').to_verilog()
        self.assertIn('(* ram_style = "distributed" *)', v)

    def test_ultra_attribute(self):
        v = _RamModule(style='ultra').to_verilog()
        self.assertIn('(* ram_style = "ultra" *)', v)


if __name__ == '__main__':
    unittest.main()
