"""Tests for parameterized port arrays (lists of Signals)."""
import unittest

from veripy.signal import Input, Output, Register, Signal
from veripy.context import comb, always
from veripy import posedge
from veripy.decorator import module
from veripy.module import Module
from veripy.lower import lower_module
from veripy.backend_verilog import emit_verilog


# --- Module definitions at module level so inspect.getsource works ---

@module
def mux4(width=8):
    sel     = Input(2)
    data_in = [Input(width) for _ in range(4)]
    out     = Output(width)

    @comb
    def drive():
        if sel == 0:
            out = data_in[0]
        elif sel == 1:
            out = data_in[1]
        elif sel == 2:
            out = data_in[2]
        else:
            out = data_in[3]


@module
def mux_n(n=4, width=8):
    sel     = Input(8)
    data_in = [Input(width) for _ in range(n)]
    out     = Output(width)

    @comb
    def drive():
        out = data_in[0]
        for i in range(n):
            if sel == i:
                out = data_in[i]


@module
def fanout(n=4, width=8):
    data_in  = Input(width)
    data_out = [Output(width) for _ in range(n)]

    @comb
    def drive():
        for i in range(n):
            data_out[i] = data_in


class TestPortArraySignalNaming(unittest.TestCase):
    def test_signals_expanded_with_indices(self):
        m = mux4(width=8)
        sigs = m._signals()
        self.assertIn('data_in_0', sigs)
        self.assertIn('data_in_1', sigs)
        self.assertIn('data_in_2', sigs)
        self.assertIn('data_in_3', sigs)
        self.assertNotIn('data_in', sigs)

    def test_signal_names_set(self):
        m = mux4(width=8)
        self.assertEqual(m.data_in[0].name, 'data_in_0')
        self.assertEqual(m.data_in[3].name, 'data_in_3')

    def test_signal_kinds(self):
        m = mux4(width=8)
        for sig in m.data_in:
            self.assertEqual(sig._kind, 'input')

    def test_output_array_naming(self):
        m = fanout(n=3, width=4)
        sigs = m._signals()
        self.assertIn('data_out_0', sigs)
        self.assertIn('data_out_1', sigs)
        self.assertIn('data_out_2', sigs)


class TestPortArrayClassBased(unittest.TestCase):
    def test_class_based_port_array(self):
        class Mux2(Module):
            def __init__(self, width=8):
                self.sel = Input(1)
                self.data_in = [Input(width), Input(width)]
                self.out = Output(width)
                super().__init__()

                @self.comb
                def drive():
                    if self.sel:
                        self.out = self.data_in[1]
                    else:
                        self.out = self.data_in[0]

        m = Mux2(width=4)
        sigs = m._signals()
        self.assertIn('data_in_0', sigs)
        self.assertIn('data_in_1', sigs)
        self.assertEqual(m.data_in[0].name, 'data_in_0')


class TestPortArrayLowering(unittest.TestCase):
    def test_ports_emitted_flat(self):
        m = mux4(width=8)
        ir = lower_module(m, 'mux4')
        port_names = [p.name for p in ir.ports]
        self.assertIn('data_in_0', port_names)
        self.assertIn('data_in_1', port_names)
        self.assertIn('data_in_2', port_names)
        self.assertIn('data_in_3', port_names)
        self.assertNotIn('data_in', port_names)

    def test_port_directions(self):
        m = mux4(width=8)
        ir = lower_module(m, 'mux4')
        port_map = {p.name: p.direction for p in ir.ports}
        for i in range(4):
            self.assertEqual(port_map[f'data_in_{i}'], 'input')
        self.assertEqual(port_map['out'], 'output')

    def test_fanout_output_ports(self):
        m = fanout(n=3, width=4)
        ir = lower_module(m, 'fanout')
        port_names = [p.name for p in ir.ports]
        for i in range(3):
            self.assertIn(f'data_out_{i}', port_names)

    def test_subscript_access_in_comb(self):
        """data_in[i] in @comb resolves to data_in_i signal."""
        m = mux4(width=8)
        ir = lower_module(m, 'mux4')
        # Should produce valid IR without errors
        self.assertTrue(len(ir.comb_blocks) > 0)

    def test_for_loop_unroll_with_subscript(self):
        """for i in range(n): data_out[i] = data_in unrolls correctly."""
        m = fanout(n=3, width=4)
        ir = lower_module(m, 'fanout')
        self.assertTrue(len(ir.comb_blocks) > 0)


class TestPortArrayVerilog(unittest.TestCase):
    def test_verilog_has_flat_ports(self):
        m = mux4(width=8)
        ir = lower_module(m, 'mux4')
        v = emit_verilog(ir)
        for i in range(4):
            self.assertIn(f'data_in_{i}', v)
        self.assertNotIn('data_in [', v)  # no array port syntax

    def test_verilog_compiles(self):
        import subprocess, tempfile, os
        m = mux4(width=8)
        ir = lower_module(m, 'mux4')
        v = emit_verilog(ir)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'mux4.v')
            out = os.path.join(d, 'mux4.out')
            with open(src, 'w') as f:
                f.write(v)
            r = subprocess.run(['iverilog', '-o', out, src],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_fanout_verilog_compiles(self):
        import subprocess, tempfile, os
        m = fanout(n=4, width=8)
        ir = lower_module(m, 'fanout')
        v = emit_verilog(ir)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'fanout.v')
            out = os.path.join(d, 'fanout.out')
            with open(src, 'w') as f:
                f.write(v)
            r = subprocess.run(['iverilog', '-o', out, src],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


class TestPortArrayIteration(unittest.TestCase):
    def test_for_in_port_array(self):
        """for sig in self.data_in: unrolls over port array signals."""
        @module
        def reducer(n=4, width=8):
            data_in = [Input(width) for _ in range(n)]
            out     = Output(width)

            @comb
            def drive():
                out = 0
                for sig in data_in:
                    out = out | sig

        m = reducer(n=3, width=8)
        ir = lower_module(m, 'reducer')
        self.assertTrue(len(ir.comb_blocks) > 0)
        v = emit_verilog(ir)
        for i in range(3):
            self.assertIn(f'data_in_{i}', v)


if __name__ == '__main__':
    unittest.main()
