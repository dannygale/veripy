"""Tests for Signal, Cat, Mux primitives."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy.signal import Signal, Input, Output, Register


class TestSignal(unittest.TestCase):
    def test_width_masking(self):
        s = Signal(4)
        s._assign(0xFF)
        s._tick()
        self.assertEqual(s._val, 0xF)

    def test_arithmetic(self):
        a, b = Signal(8), Signal(8)
        a._val, b._val = 100, 50
        self.assertEqual(a + b, 150)
        self.assertEqual(a - b, 50)

    def test_overflow_wraps(self):
        s = Signal(4)
        s._val = 15
        self.assertEqual(s + 1, 0)  # 4-bit wrap

    def test_underflow_wraps(self):
        s = Signal(4)
        s._val = 0
        self.assertEqual(s - 1, 15)

    def test_bitwise(self):
        a, b = Signal(8), Signal(8)
        a._val, b._val = 0xAA, 0x0F
        self.assertEqual(a & b, 0x0A)
        self.assertEqual(a | b, 0xAF)
        self.assertEqual(a ^ b, 0xA5)
        self.assertEqual(~a, 0x55)

    def test_comparison(self):
        a, b = Signal(8), Signal(8)
        a._val, b._val = 5, 10
        self.assertTrue(a < b)
        self.assertTrue(a != b)
        self.assertFalse(a == b)

    def test_bool(self):
        s = Signal(8)
        s._val = 0
        self.assertFalse(bool(s))
        s._val = 1
        self.assertTrue(bool(s))

    def test_nba_and_tick(self):
        s = Signal(8)
        s._val = 10
        s._assign(20)
        self.assertEqual(s._val, 10)  # not yet applied
        s._tick()
        self.assertEqual(s._val, 20)  # now applied

    def test_nba_masks(self):
        s = Signal(4)
        s._assign(0xFF)
        s._tick()
        self.assertEqual(s._val, 0xF)

    def test_bit_slice(self):
        s = Signal(8)
        s._val = 0b11010110
        self.assertEqual(s[0], 0)
        self.assertEqual(s[1], 1)
        self.assertEqual(s[7:4], 0b1101)
        self.assertEqual(s[3:0], 0b0110)

    def test_kinds(self):
        self.assertEqual(Input()._kind, 'input')
        self.assertEqual(Output()._kind, 'output')
        self.assertEqual(Register()._kind, 'reg')


class TestConcat(unittest.TestCase):
    def test_list_concat_two(self):
        a, b = Signal(4), Signal(4)
        a._val, b._val = 0xA, 0xB
        out = Signal(8)
        out._assign([a, b])
        out._tick()
        self.assertEqual(out._val, 0xBA)

    def test_list_concat_three(self):
        a, b, c = Signal(4), Signal(4), Signal(4)
        a._val, b._val, c._val = 0x1, 0x2, 0x3
        out = Signal(12)
        out._assign([a, b, c])
        out._tick()
        self.assertEqual(out._val, 0x321)


class TestReverseShift(unittest.TestCase):
    def test_rlshift_signal(self):
        s = Signal(4)
        s._val = 2
        self.assertEqual(1 << s, 4)  # 1 << 2

    def test_rrshift_signal(self):
        s = Signal(4)
        s._val = 3
        self.assertEqual(24 >> s, 3)  # 24 >> 3

    def test_rlshift_slice(self):
        s = Signal(8)
        s._val = 0x23
        sl = s[3:0]  # value 3
        self.assertEqual(1 << sl, 8)  # 1 << 3

    def test_rrshift_slice(self):
        s = Signal(8)
        s._val = 0x12
        sl = s[3:0]  # value 2
        self.assertEqual(16 >> sl, 4)  # 16 >> 2


class TestTernaryMux(unittest.TestCase):
    def test_ternary_select(self):
        sel = Signal(1)
        a, b = Signal(8), Signal(8)
        a._val, b._val = 0xAA, 0xBB
        sel._val = 0
        self.assertEqual(a if sel else b, b)
        sel._val = 1
        self.assertEqual(a if sel else b, a)


class TestNegedge(unittest.TestCase):
    def test_negedge_sim(self):
        """Module with @self.negedge captures on falling edge."""
        from veripy import Module, Input, Output, Register, posedge, negedge

        class FallingEdge(Module):
            def __init__(self):
                self.clk = Input()
                self.d   = Input(8)
                self.q   = Output(8)
                self.r   = Register(8)
                super().__init__()

                @self.comb
                def drive():
                    self.q = self.r

                @self.negedge(self.clk)
                def capture():
                    self.r = self.d

        from veripy.verify import TestBench, initial as _initial

        class _T(TestBench):
            def create_module(self): return FallingEdge()
            def test_it(self):
                dut = self.dut
                @_initial
                def stim():
                    dut.d = 42; dut.clk = 1; yield 1
                    dut.clk = 0; yield 1
                self.assertEqual(self.get('q'), 42)

        import unittest
        suite = unittest.TestLoader().loadTestsFromName('test_it', _T)
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(len(result.failures) + len(result.errors), 0)

    def test_negedge_verilog_emission(self):
        from veripy import Module, Input, Output, Register

        class NegMod(Module):
            def __init__(self):
                self.clk = Input()
                self.d   = Input(8)
                self.q   = Output(8)
                self.r   = Register(8)
                super().__init__()

                @self.comb
                def drive():
                    self.q = self.r

                @self.negedge(self.clk)
                def capture():
                    self.r = self.d

        v = NegMod().to_verilog()
        self.assertIn('always @(negedge clk)', v)

    def test_combined_sensitivity_verilog(self):
        from veripy import Module, Input, Output, Register, posedge, negedge

        class AsyncRst(Module):
            def __init__(self):
                self.clk   = Input()
                self.rst_n = Input()
                self.d     = Input(8)
                self.q     = Output(8)
                self.r     = Register(8)
                super().__init__()

                @self.comb
                def drive():
                    self.q = self.r

                @self.always(posedge(self.clk) | negedge(self.rst_n))
                def logic():
                    if not self.rst_n:
                        self.r = 0
                    else:
                        self.r = self.d

        v = AsyncRst().to_verilog()
        self.assertIn('always @(posedge clk or negedge rst_n)', v)


if __name__ == '__main__':
    unittest.main()
