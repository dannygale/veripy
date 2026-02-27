"""Tests for Interface bundles."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, Input, Output, Interface


class Handshake(Interface):
    valid = ('input', 1)
    ready = ('output', 1)
    data  = ('input', 8)


class Consumer(Module):
    def __init__(self):
        self.clock = Input()
        self.bus   = Handshake()
        self.out   = Output(8)
        super().__init__()

        @self.comb
        def logic():
            self.bus.ready = 1
            self.out = self.bus.data


class TestInterfaceSimulation(unittest.TestCase):
    def test_read_interface_signal(self):
        c = Consumer()
        c.bus.data.set(42)
        c.tick()
        self.assertEqual(int(c.out), 42)

    def test_write_interface_signal(self):
        c = Consumer()
        c.tick()
        self.assertEqual(int(c.bus.ready), 1)

    def test_signals_flattened(self):
        c = Consumer()
        sigs = c._signals()
        self.assertIn('bus_data', sigs)
        self.assertIn('bus_ready', sigs)
        self.assertIn('bus_valid', sigs)


class TestInterfaceVerilog(unittest.TestCase):
    def setUp(self):
        self.v = Consumer().to_verilog()

    def test_ports_have_prefix(self):
        self.assertIn('input [7:0] bus_data', self.v)
        self.assertIn('input bus_valid', self.v)
        self.assertIn('output bus_ready', self.v)

    def test_assigns_use_prefix(self):
        self.assertIn('bus_ready = 1', self.v)
        self.assertIn('out = bus_data', self.v)


class TestMultipleInterfaces(unittest.TestCase):
    def test_two_interfaces(self):
        class TwoPort(Module):
            def __init__(self):
                self.rx = Handshake()
                self.tx = Handshake()
                super().__init__()
                @self.comb
                def wire():
                    self.tx.ready = self.rx.valid

        m = TwoPort()
        sigs = m._signals()
        self.assertIn('rx_data', sigs)
        self.assertIn('tx_data', sigs)
        v = m.to_verilog()
        self.assertIn('rx_valid', v)
        self.assertIn('tx_ready', v)


if __name__ == '__main__':
    unittest.main()
