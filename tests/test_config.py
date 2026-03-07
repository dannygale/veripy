"""Tests for veripy.config: schema, loader, validation, and directory search."""

import os
import sys
import tempfile
import textwrap
import unittest


class TestLoadConfig(unittest.TestCase):
    def _write_toml(self, d, content):
        path = os.path.join(d, "veripy.toml")
        with open(path, "w") as f:
            f.write(textwrap.dedent(content))
        return path

    def test_returns_none_when_no_file(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                result = load_config()
                self.assertIsNone(result)
            finally:
                os.chdir(old)

    def test_loads_minimal_toml(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            self._write_toml(d, """\
                [project]
                name = "mydesign"
            """)
            cfg = load_config(os.path.join(d, "veripy.toml"))
        self.assertEqual(cfg["project"]["name"], "mydesign")

    def test_defaults_applied(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            self._write_toml(d, "[project]\nname = \"x\"\n")
            cfg = load_config(os.path.join(d, "veripy.toml"))
        self.assertEqual(cfg["build"]["output"], "rtl/")
        self.assertEqual(cfg["test"]["path"], "tests/")
        self.assertEqual(cfg["build"]["params"], {})

    def test_build_section_overrides_defaults(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            self._write_toml(d, """\
                [build]
                top = "src/top.py"
                output = "out/"
            """)
            cfg = load_config(os.path.join(d, "veripy.toml"))
        self.assertEqual(cfg["build"]["top"], "src/top.py")
        self.assertEqual(cfg["build"]["output"], "out/")

    def test_build_params_parsed(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            self._write_toml(d, """\
                [build]
                top = "src/top.py"
                params = { width = 16 }
            """)
            cfg = load_config(os.path.join(d, "veripy.toml"))
        self.assertEqual(cfg["build"]["params"], {"width": 16})

    def test_path_and_dir_set(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            p = self._write_toml(d, "[project]\nname = \"x\"\n")
            cfg = load_config(p)
        self.assertEqual(cfg["_path"], p)
        self.assertEqual(cfg["_dir"], d)

    def test_invalid_top_extension_exits(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            self._write_toml(d, "[build]\ntop = \"src/top.v\"\n")
            with self.assertRaises(SystemExit):
                load_config(os.path.join(d, "veripy.toml"))

    def test_invalid_fpga_family_exits(self):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            self._write_toml(d, "[fpga]\nfamily = \"bogus\"\n")
            with self.assertRaises(SystemExit):
                load_config(os.path.join(d, "veripy.toml"))

    def test_find_config_walks_up(self):
        from veripy.config import _find_config
        with tempfile.TemporaryDirectory() as d:
            toml = os.path.join(d, "veripy.toml")
            open(toml, "w").close()
            nested = os.path.join(d, "a", "b", "c")
            os.makedirs(nested)
            old = os.getcwd()
            os.chdir(nested)
            try:
                found = _find_config()
                self.assertEqual(os.path.realpath(found), os.path.realpath(toml))
            finally:
                os.chdir(old)

    def test_find_config_returns_none_when_absent(self):
        from veripy.config import _find_config
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                self.assertIsNone(_find_config())
            finally:
                os.chdir(old)


class TestCmdInit(unittest.TestCase):
    def test_init_creates_expected_files(self):
        import argparse
        from veripy.cli import cmd_init
        with tempfile.TemporaryDirectory() as d:
            args = argparse.Namespace(name="myproj", output=os.path.join(d, "myproj"))
            cmd_init(args)
            dest = os.path.join(d, "myproj")
            self.assertTrue(os.path.isfile(os.path.join(dest, "veripy.toml")))
            self.assertTrue(os.path.isfile(os.path.join(dest, "src", "top.py")))
            self.assertTrue(os.path.isdir(os.path.join(dest, "tests")))
            self.assertTrue(os.path.isdir(os.path.join(dest, "rtl")))

    def test_init_toml_contains_project_name(self):
        import argparse
        from veripy.cli import cmd_init
        with tempfile.TemporaryDirectory() as d:
            args = argparse.Namespace(name="coolchip", output=os.path.join(d, "coolchip"))
            cmd_init(args)
            with open(os.path.join(d, "coolchip", "veripy.toml")) as f:
                content = f.read()
            self.assertIn('name = "coolchip"', content)

    def test_init_exits_if_toml_exists(self):
        import argparse
        from veripy.cli import cmd_init
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "proj")
            os.makedirs(dest)
            open(os.path.join(dest, "veripy.toml"), "w").close()
            args = argparse.Namespace(name="proj", output=dest)
            with self.assertRaises(SystemExit):
                cmd_init(args)

    def test_init_toml_is_valid(self):
        import argparse
        from veripy.cli import cmd_init
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "proj")
            args = argparse.Namespace(name="proj", output=dest)
            cmd_init(args)
            cfg = load_config(os.path.join(dest, "veripy.toml"))
            self.assertEqual(cfg["project"]["name"], "proj")
            self.assertEqual(cfg["build"]["top"], "src/top.py")


if __name__ == "__main__":
    unittest.main()
