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


class TestTernaryMux(unittest.TestCase):
    def test_ternary_select(self):
        sel = Signal(1)
        a, b = Signal(8), Signal(8)
        a._val, b._val = 0xAA, 0xBB
        sel._val = 0
        self.assertEqual(a if sel else b, b)
        sel._val = 1
        self.assertEqual(a if sel else b, a)


if __name__ == '__main__':
    unittest.main()
