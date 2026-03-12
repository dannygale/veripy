"""Tests for Inout port type."""
import unittest
from veripy import Module, Input, Output, Register, Inout
from veripy.verify import TestBench, initial


class _BidirBuf(Module):
    """Bidirectional buffer: reads bus value, captures on clock edge."""
    def __init__(self):
        self.clock  = Input()
        self.bus    = Inout(8)
        self.q      = Output(8)
        self.r      = Register(8)
        super().__init__()

    def rtl(self):
        @self.comb
        def read():
            self.q = self.bus

        @self.posedge(self.clock)
        def capture():
            self.r = self.bus


class TestInoutVerilog(unittest.TestCase):
    def setUp(self):
        self.v = _BidirBuf().to_verilog()

    def test_inout_port_declared(self):
        self.assertIn('inout [7:0] bus', self.v)

    def test_input_ports_present(self):
        self.assertIn('input clock', self.v)

    def test_output_ports_present(self):
        self.assertIn('output [7:0] q', self.v)

    def test_port_order(self):
        # inputs before inout before outputs
        i_pos   = self.v.index('input')
        io_pos  = self.v.index('inout')
        o_pos   = self.v.index('output')
        self.assertLess(i_pos, io_pos)
        self.assertLess(io_pos, o_pos)


class TestInoutSim(TestBench):
    def create_module(self): return _BidirBuf()

    def test_read_bus(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.bus = 0xAB; yield 10
            self.assertEqual(self.get('q'), 0xAB)

    def test_drive_bus_changes(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.bus = 0x55; yield 10
            self.assertEqual(self.get('q'), 0x55)
            dut.bus = 0xAA; yield 10
            self.assertEqual(self.get('q'), 0xAA)

    def test_capture_on_clock(self):
        dut = self.dut; self.clock('clock', 10)
        @initial
        def _():
            dut.bus = 0xCD; yield 10
            self.assertEqual(self.get('r'), 0xCD)


class TestInoutExport(unittest.TestCase):
    def test_importable(self):
        from veripy import Inout
        sig = Inout(8)
        self.assertEqual(sig._kind, 'inout')
        self.assertEqual(sig.width, 8)

    def test_default_width(self):
        from veripy import Inout
        self.assertEqual(Inout().width, 1)


if __name__ == '__main__':
    unittest.main()
