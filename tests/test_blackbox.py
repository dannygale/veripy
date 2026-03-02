"""Tests for BlackBox: port-only declaration, Verilog instantiation, parameter forwarding."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, BlackBox, Input, Output, Register


class SimpleBRAM(BlackBox):
    def __init__(self, data_width=32, addr_width=10):
        self.clk  = Input()
        self.we   = Input()
        self.addr = Input(addr_width)
        self.din  = Input(data_width)
        self.dout = Output(data_width)
        super().__init__(verilog_module_name='RAMB36E2')


class GenericPLL(BlackBox):
    """BlackBox without custom verilog_module_name — uses snake_case class name."""
    def __init__(self, mult=8, div=1):
        self.clk_in  = Input()
        self.clk_out = Output()
        self.locked  = Output()
        super().__init__()


class TopWithBRAM(Module):
    def __init__(self):
        self.clk  = Input()
        self.we   = Input()
        self.addr = Input(10)
        self.din  = Input(32)
        self.dout = Output(32)
        self.mem  = SimpleBRAM(data_width=32, addr_width=10)
        super().__init__()

        @self.comb
        def wire():
            self.mem.clk  = self.clk
            self.mem.we   = self.we
            self.mem.addr = self.addr
            self.mem.din  = self.din
            self.dout     = self.mem.dout


class TestBlackBoxDeclaration(unittest.TestCase):
    def test_is_blackbox_flag(self):
        b = SimpleBRAM()
        self.assertTrue(b._is_blackbox)

    def test_is_module_subclass(self):
        b = SimpleBRAM()
        self.assertIsInstance(b, Module)

    def test_custom_verilog_name(self):
        b = SimpleBRAM()
        self.assertEqual(b._verilog_module_name, 'RAMB36E2')

    def test_default_verilog_name(self):
        b = GenericPLL()
        self.assertFalse(hasattr(b, '_verilog_module_name'))

    def test_ports_discovered(self):
        b = SimpleBRAM()
        sigs = b._signals()
        self.assertIn('clk', sigs)
        self.assertIn('dout', sigs)

    def test_params_captured(self):
        b = SimpleBRAM(data_width=16, addr_width=8)
        self.assertEqual(b._params['data_width'], 16)
        self.assertEqual(b._params['addr_width'], 8)


class TestBlackBoxVerilogEmission(unittest.TestCase):
    def setUp(self):
        self.v = TopWithBRAM().to_verilog()

    def test_instance_uses_custom_module_name(self):
        self.assertIn('RAMB36E2', self.v)

    def test_instance_has_params(self):
        self.assertIn('#(.data_width(32)', self.v)

    def test_instance_has_port_connections(self):
        self.assertIn('.clk(mem_clk)', self.v)
        self.assertIn('.dout(mem_dout)', self.v)

    def test_no_blackbox_module_definition(self):
        """BlackBox should NOT emit its own module/endmodule block."""
        self.assertNotIn('module RAMB36E2', self.v)
        self.assertNotIn('module simple_bram', self.v)
        # Only the top module definition should exist
        lines = [l for l in self.v.split('\n') if l.strip().startswith('module ')]
        self.assertEqual(len(lines), 1)
        self.assertIn('topwithbram', lines[0])


class TestBlackBoxDefaultName(unittest.TestCase):
    """BlackBox without custom name uses snake_case of class name."""
    def test_snake_case_name(self):
        class TopPLL(Module):
            def __init__(self):
                self.clk_in  = Input()
                self.clk_out = Output()
                self.locked  = Output()
                self.pll     = GenericPLL(mult=4)
                super().__init__()

                @self.comb
                def wire():
                    self.pll.clk_in  = self.clk_in
                    self.clk_out     = self.pll.clk_out
                    self.locked      = self.pll.locked

        v = TopPLL().to_verilog()
        self.assertIn('generic_pll', v)
        self.assertIn('#(.mult(4)', v)


class TestBlackBoxCLISkip(unittest.TestCase):
    """Verify _collect() skips blackbox sub-modules."""
    def test_submodules_includes_blackbox(self):
        top = TopWithBRAM()
        subs = top._submodules()
        self.assertIn('mem', subs)
        self.assertTrue(subs['mem']._is_blackbox)


if __name__ == '__main__':
    unittest.main()
