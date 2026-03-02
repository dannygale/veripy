"""Tests for IP packaging: metadata, discovery, scaffold generation."""

import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from unittest.mock import patch, MagicMock
from veripy.packaging import IpMetadata, discover, scaffold, ENTRY_POINT_GROUP


class TestIpMetadata(unittest.TestCase):
    def test_fields(self):
        m = IpMetadata(name='axi', version='1.0.0', module_path='veripy_axi')
        self.assertEqual(m.name, 'axi')
        self.assertEqual(m.version, '1.0.0')
        self.assertEqual(m.module_path, 'veripy_axi')
        self.assertEqual(m.description, '')
        self.assertEqual(m.ip_modules, [])

    def test_load(self):
        m = IpMetadata(name='os', version='0', module_path='os')
        mod = m.load()
        import os as real_os
        self.assertIs(mod, real_os)


class TestDiscover(unittest.TestCase):
    def _make_ep(self, name, value, dist_name='veripy-test', version='1.0.0', summary='Test IP'):
        ep = MagicMock()
        ep.name = name
        ep.value = value
        ep.dist = MagicMock()
        ep.dist.name = dist_name
        ep.dist.metadata = {'Version': version, 'Summary': summary}
        return ep

    @patch('veripy.packaging.entry_points')
    @patch('veripy.packaging.metadata')
    def test_empty(self, mock_meta, mock_eps):
        mock_eps.return_value = MagicMock()
        mock_eps.return_value.select = MagicMock(return_value=[])
        result = discover()
        self.assertEqual(result, [])

    @patch('veripy.packaging.entry_points')
    @patch('veripy.packaging.metadata')
    def test_discovers_packages(self, mock_meta, mock_eps):
        ep1 = self._make_ep('axi', 'veripy_axi', summary='AXI IP')
        ep2 = self._make_ep('spi', 'veripy_spi', summary='SPI IP')
        mock_eps.return_value = MagicMock()
        mock_eps.return_value.select = MagicMock(return_value=[ep2, ep1])
        result = discover()
        self.assertEqual(len(result), 2)
        # sorted by name
        self.assertEqual(result[0].name, 'axi')
        self.assertEqual(result[1].name, 'spi')
        self.assertEqual(result[0].module_path, 'veripy_axi')
        self.assertEqual(result[0].description, 'AXI IP')

    @patch('veripy.packaging.entry_points')
    @patch('veripy.packaging.metadata')
    def test_dict_style_entry_points(self, mock_meta, mock_eps):
        """Python 3.9-3.11 returns dict from entry_points()."""
        ep = self._make_ep('uart', 'veripy_uart', summary='UART IP')
        mock_eps.return_value = {ENTRY_POINT_GROUP: [ep]}
        result = discover()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'uart')


class TestScaffold(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_creates_structure(self):
        path = scaffold('myip', output_dir=self.tmpdir)
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.path.isfile(os.path.join(path, 'pyproject.toml')))
        self.assertTrue(os.path.isfile(os.path.join(path, 'README.md')))
        self.assertTrue(os.path.isfile(os.path.join(path, 'veripy_myip', '__init__.py')))

    def test_pyproject_content(self):
        path = scaffold('myip', output_dir=self.tmpdir)
        with open(os.path.join(path, 'pyproject.toml')) as f:
            content = f.read()
        self.assertIn('name = "veripy-myip"', content)
        self.assertIn('veripy-hdl', content)
        self.assertIn('[project.entry-points."veripy.ip"]', content)
        self.assertIn('myip = "veripy_myip"', content)

    def test_idempotent(self):
        scaffold('myip', output_dir=self.tmpdir)
        scaffold('myip', output_dir=self.tmpdir)  # should not raise
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmpdir, 'myip', 'pyproject.toml')))


if __name__ == '__main__':
    unittest.main()
