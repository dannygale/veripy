"""Tests for dual-port and true dual-port memory variants."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Register, DualPortMem, TrueDualPortMem


# ── Test modules ─────────────────────────────────────────────────────

class _DualPortRam(Module):
    """Simple dual-port RAM: 1 write port + 1 read port."""
    def __init__(self, depth=16, width=8, style=None):
        self.clock = Input()
        self.we    = Input()
        self.waddr = Input(4)
        self.wdata = Input(width)
        self.raddr = Input(4)
        self.rdata = Output(width)
        self.ram   = DualPortMem(depth, width, style=style,
                                 clock=self.clock, we=self.we,
                                 waddr=self.waddr, wdata=self.wdata,
                                 raddr=self.raddr, rdata=self.rdata)
        super().__init__()


class _TrueDualPortRam(Module):
    """True dual-port RAM: 2 read/write ports."""
    def __init__(self, depth=16, width=8, style=None):
        self.clka  = Input()
        self.wea   = Input()
        self.addra = Input(4)
        self.dina  = Input(width)
        self.douta = Output(width)
        self.clkb  = Input()
        self.web   = Input()
        self.addrb = Input(4)
        self.dinb  = Input(width)
        self.doutb = Output(width)
        self.ram   = TrueDualPortMem(depth, width, style=style,
                                     clka=self.clka, wea=self.wea,
                                     addra=self.addra, dina=self.dina,
                                     douta=self.douta,
                                     clkb=self.clkb, web=self.web,
                                     addrb=self.addrb, dinb=self.dinb,
                                     doutb=self.doutb)
        super().__init__()


# ── Unit tests ───────────────────────────────────────────────────────

class TestDualPortMemConstruction(unittest.TestCase):
    def test_default_no_style(self):
        m = DualPortMem(16, 8)
        self.assertIsNone(m.style)

    def test_block_style(self):
        m = DualPortMem(16, 8, style='block')
        self.assertEqual(m.style, 'block')

    def test_invalid_style_raises(self):
        with self.assertRaises(ValueError):
            DualPortMem(16, 8, style='invalid')


class TestTrueDualPortMemConstruction(unittest.TestCase):
    def test_default_no_style(self):
        m = TrueDualPortMem(16, 8)
        self.assertIsNone(m.style)

    def test_ultra_style(self):
        m = TrueDualPortMem(16, 8, style='ultra')
        self.assertEqual(m.style, 'ultra')

    def test_invalid_style_raises(self):
        with self.assertRaises(ValueError):
            TrueDualPortMem(16, 8, style='invalid')


class TestDualPortVerilog(unittest.TestCase):
    def test_always_block_pattern(self):
        v = _DualPortRam().to_verilog()
        self.assertIn('always @(posedge clock)', v)
        self.assertIn('if (we)', v)
        self.assertIn('ram[waddr] <= wdata', v)
        self.assertIn('rdata <= ram[raddr]', v)

    def test_rdata_is_reg(self):
        v = _DualPortRam().to_verilog()
        self.assertIn('output reg', v)

    def test_mem_declaration(self):
        v = _DualPortRam().to_verilog()
        self.assertIn('reg [7:0] ram [0:15]', v)

    def test_block_style_attribute(self):
        v = _DualPortRam(style='block').to_verilog()
        self.assertIn('(* ram_style = "block" *) reg [7:0] ram', v)

    def test_no_style_no_attribute(self):
        v = _DualPortRam().to_verilog()
        self.assertNotIn('ram_style', v)


class TestTrueDualPortVerilog(unittest.TestCase):
    def test_two_always_blocks(self):
        v = _TrueDualPortRam().to_verilog()
        self.assertIn('always @(posedge clka)', v)
        self.assertIn('always @(posedge clkb)', v)

    def test_port_a_pattern(self):
        v = _TrueDualPortRam().to_verilog()
        self.assertIn('if (wea)', v)
        self.assertIn('ram[addra] <= dina', v)
        self.assertIn('douta <= ram[addra]', v)

    def test_port_b_pattern(self):
        v = _TrueDualPortRam().to_verilog()
        self.assertIn('if (web)', v)
        self.assertIn('ram[addrb] <= dinb', v)
        self.assertIn('doutb <= ram[addrb]', v)

    def test_outputs_are_reg(self):
        v = _TrueDualPortRam().to_verilog()
        # Both douta and doutb should be output reg
        lines = v.split('\n')
        output_reg_lines = [l for l in lines if 'output reg' in l]
        self.assertEqual(len(output_reg_lines), 2)

    def test_distributed_style(self):
        v = _TrueDualPortRam(style='distributed').to_verilog()
        self.assertIn('(* ram_style = "distributed" *)', v)


class TestDualPortSimulation(unittest.TestCase):
    def test_write_then_read(self):
        m = _DualPortRam()
        # Write value 42 to address 3
        m.ram._data[3] = 42
        # Verify internal state
        self.assertEqual(m.ram._data[3], 42)

    def test_tick_applies_write(self):
        m = _DualPortRam()
        m.ram._pending_write = (5, 99)
        m.ram._tick()
        self.assertEqual(m.ram._data[5], 99)
        self.assertIsNone(m.ram._pending_write)


class TestTrueDualPortSimulation(unittest.TestCase):
    def test_tick_applies_both_ports(self):
        m = _TrueDualPortRam()
        m.ram._pending_a = (1, 10)
        m.ram._pending_b = (2, 20)
        m.ram._tick()
        self.assertEqual(m.ram._data[1], 10)
        self.assertEqual(m.ram._data[2], 20)
        self.assertIsNone(m.ram._pending_a)
        self.assertIsNone(m.ram._pending_b)


if __name__ == '__main__':
    unittest.main()
