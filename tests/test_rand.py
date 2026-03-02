"""Tests for constrained random stimulus generation."""
import unittest

try:
    import constrainedrandom
    _has_cr = True
except ImportError:
    _has_cr = False

from veripy.rand import Rand, Range


@unittest.skipUnless(_has_cr, 'constrainedrandom not installed')
class TestRand(unittest.TestCase):

    def test_basic_range(self):
        r = Rand(x=Range(0, 255))
        r.randomize()
        self.assertGreaterEqual(r.x, 0)
        self.assertLessEqual(r.x, 255)

    def test_exclude(self):
        r = Rand(x=Range(0, 10, exclude=[5]))
        for _ in range(50):
            r.randomize()
            self.assertNotEqual(r.x, 5)

    def test_multiple_fields(self):
        r = Rand(addr=Range(0, 0xFFF), data=Range(0, 255))
        r.randomize()
        self.assertIn('addr', r.as_dict())
        self.assertIn('data', r.as_dict())

    def test_custom_constraint(self):
        r = Rand(a=Range(0, 15), b=Range(0, 15))
        r.add_constraint(lambda a, b: a + b <= 10, 'a', 'b')
        for _ in range(30):
            r.randomize()
            self.assertLessEqual(r.a + r.b, 10)

    def test_as_dict(self):
        r = Rand(x=Range(0, 7))
        r.randomize()
        d = r.as_dict()
        self.assertEqual(d['x'], r.x)


if __name__ == '__main__':
    unittest.main()
