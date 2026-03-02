"""Tests for Project class: module tracking, dependency ordering, Tcl generation."""

import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import Module, BlackBox, Input, Output, Register, Project


# ── Test modules ─────────────────────────────────────────────────────

class Inner(Module):
    def __init__(self):
        self.clock = Input()
        self.d = Input(8)
        self.q = Output(8)
        self.r = Register(8)
        super().__init__()

        @self.posedge(self.clock)
        def latch():
            self.r = self.d

        @self.comb
        def out():
            self.q = self.r


class Outer(Module):
    def __init__(self):
        self.clock = Input()
        self.d = Input(8)
        self.q = Output(8)
        self.sub = Inner()
        super().__init__()

        @self.comb
        def wire():
            self.sub.clock = self.clock
            self.sub.d = self.d
            self.q = self.sub.q


class VendorIP(BlackBox):
    def __init__(self):
        self.clk = Input()
        self.out = Output()
        super().__init__(verilog_module_name='VENDOR_IP')


class TopWithBlackBox(Module):
    def __init__(self):
        self.clock = Input()
        self.out = Output()
        self.ip = VendorIP()
        self.sub = Inner()
        super().__init__()

        @self.comb
        def wire():
            self.ip.clk = self.clock
            self.sub.clock = self.clock
            self.sub.d = 0
            self.out = self.ip.out


# ── Tests ────────────────────────────────────────────────────────────

class TestProjectBasic(unittest.TestCase):
    def test_single_module(self):
        proj = Project()
        proj.add(Inner())
        mods = proj.modules()
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0][0], 'inner')

    def test_custom_name(self):
        proj = Project()
        proj.add(Inner(), name='my_reg')
        mods = proj.modules()
        self.assertEqual(mods[0][0], 'my_reg')

    def test_add_returns_self(self):
        proj = Project()
        result = proj.add(Inner())
        self.assertIs(result, proj)


class TestProjectDependencyOrder(unittest.TestCase):
    def test_sub_before_top(self):
        proj = Project()
        proj.add(Outer())
        mods = proj.modules()
        names = [n for n, _ in mods]
        self.assertEqual(names, ['inner', 'outer'])

    def test_dedup(self):
        """Same sub-module type used in two tops appears once."""
        proj = Project()
        proj.add(Outer(), name='top_a')
        proj.add(Outer(), name='top_b')
        mods = proj.modules()
        names = [n for n, _ in mods]
        self.assertEqual(names.count('inner'), 1)


class TestProjectBlackBox(unittest.TestCase):
    def test_blackbox_excluded(self):
        proj = Project()
        proj.add(TopWithBlackBox())
        names = [n for n, _ in proj.modules()]
        self.assertNotIn('vendor_i_p', names)
        self.assertNotIn('VENDOR_IP', names)
        self.assertIn('inner', names)
        self.assertIn('top_with_black_box', names)


class TestProjectFileList(unittest.TestCase):
    def test_file_list_returns_verilog(self):
        proj = Project()
        proj.add(Inner())
        files = proj.file_list()
        self.assertEqual(len(files), 1)
        name, src = files[0]
        self.assertEqual(name, 'inner')
        self.assertIn('module inner', src)

    def test_file_list_order(self):
        proj = Project()
        proj.add(Outer())
        files = proj.file_list()
        names = [n for n, _ in files]
        self.assertEqual(names, ['inner', 'outer'])

    def test_file_list_verilog_valid(self):
        proj = Project()
        proj.add(Outer())
        for name, src in proj.file_list():
            self.assertIn(f'module {name}', src)
            self.assertIn('endmodule', src)


class TestProjectEmpty(unittest.TestCase):
    def test_empty_project(self):
        proj = Project()
        self.assertEqual(proj.modules(), [])
        self.assertEqual(proj.file_list(), [])


# ── Write tests ──────────────────────────────────────────────────────

class TestProjectWrite(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_write_creates_files(self):
        proj = Project()
        proj.add(Outer())
        paths = proj.write(self.tmpdir)
        self.assertEqual(len(paths), 2)
        for p in paths:
            self.assertTrue(os.path.isfile(p))

    def test_write_dependency_order(self):
        proj = Project()
        proj.add(Outer())
        paths = proj.write(self.tmpdir)
        names = [os.path.basename(p) for p in paths]
        self.assertEqual(names, ['inner.v', 'outer.v'])

    def test_write_content(self):
        proj = Project()
        proj.add(Inner())
        paths = proj.write(self.tmpdir)
        with open(paths[0]) as f:
            self.assertIn('module inner', f.read())


# ── Tcl generation tests ────────────────────────────────────────────

class TestVivadoTcl(unittest.TestCase):
    def test_read_verilog_commands(self):
        proj = Project()
        proj.add(Outer())
        tcl = proj.to_vivado_tcl('rtl')
        self.assertIn('read_verilog rtl/inner.v', tcl)
        self.assertIn('read_verilog rtl/outer.v', tcl)

    def test_top_module_set(self):
        proj = Project()
        proj.add(Outer())
        tcl = proj.to_vivado_tcl()
        self.assertIn('set_property top outer', tcl)

    def test_dependency_order(self):
        proj = Project()
        proj.add(Outer())
        tcl = proj.to_vivado_tcl('rtl')
        inner_pos = tcl.index('inner.v')
        outer_pos = tcl.index('outer.v')
        self.assertLess(inner_pos, outer_pos)

    def test_empty_project(self):
        tcl = Project().to_vivado_tcl()
        self.assertNotIn('read_verilog', tcl)
        self.assertNotIn('set_property top', tcl)


class TestQuartusTcl(unittest.TestCase):
    def test_global_assignments(self):
        proj = Project()
        proj.add(Outer())
        tcl = proj.to_quartus_tcl('rtl')
        self.assertIn('set_global_assignment -name VERILOG_FILE rtl/inner.v', tcl)
        self.assertIn('set_global_assignment -name VERILOG_FILE rtl/outer.v', tcl)

    def test_top_level_entity(self):
        proj = Project()
        proj.add(Outer())
        tcl = proj.to_quartus_tcl()
        self.assertIn('set_global_assignment -name TOP_LEVEL_ENTITY outer', tcl)


class TestDcTcl(unittest.TestCase):
    def test_analyze_commands(self):
        proj = Project()
        proj.add(Outer())
        tcl = proj.to_dc_tcl('rtl')
        self.assertIn('analyze -format verilog rtl/inner.v', tcl)
        self.assertIn('analyze -format verilog rtl/outer.v', tcl)

    def test_elaborate(self):
        proj = Project()
        proj.add(Outer())
        tcl = proj.to_dc_tcl()
        self.assertIn('elaborate outer', tcl)


if __name__ == '__main__':
    unittest.main()
