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


class TestGetTarget(unittest.TestCase):
    def _write_toml(self, d, content):
        path = os.path.join(d, "veripy.toml")
        with open(path, "w") as f:
            f.write(textwrap.dedent(content))
        return path

    def _load(self, content):
        from veripy.config import load_config
        with tempfile.TemporaryDirectory() as d:
            p = self._write_toml(d, content)
            return load_config(p)

    def test_get_target_merges_base(self):
        from veripy.config import get_target
        cfg = self._load("""\
            [build]
            top = "src/top.py"
            output = "rtl/"

            [build.targets.synth]
            output = "synth/"
            synth = true
        """)
        t = get_target(cfg, "synth")
        self.assertEqual(t["top"], "src/top.py")
        self.assertEqual(t["output"], "synth/")
        self.assertTrue(t["synth"])

    def test_get_target_merges_params(self):
        from veripy.config import get_target
        cfg = self._load("""\
            [build]
            top = "src/top.py"
            params = { width = 8, depth = 4 }

            [build.targets.wide]
            params = { width = 16 }
        """)
        t = get_target(cfg, "wide")
        self.assertEqual(t["params"], {"width": 16, "depth": 4})

    def test_get_target_unknown_exits(self):
        from veripy.config import get_target
        cfg = self._load("""\
            [build]
            top = "src/top.py"

            [build.targets.sim]
            output = "sim/"
        """)
        with self.assertRaises(SystemExit):
            get_target(cfg, "missing")

    def test_get_target_no_targets_exits(self):
        from veripy.config import get_target
        cfg = self._load("[build]\ntop = \"src/top.py\"\n")
        with self.assertRaises(SystemExit):
            get_target(cfg, "anything")

    def test_get_target_does_not_include_targets_key(self):
        from veripy.config import get_target
        cfg = self._load("""\
            [build]
            top = "src/top.py"

            [build.targets.sim]
            output = "sim/"
        """)
        t = get_target(cfg, "sim")
        self.assertNotIn("targets", t)


class TestCmdBuildSynthStripping(unittest.TestCase):
    """Test that synth=true targets strip formal properties from emitted Verilog."""

    def _make_module_file(self, d):
        src = textwrap.dedent("""\
            from veripy import module, Input, Output, Register, posedge
            from veripy.context import comb, always, assert_always

            @module
            def top():
                clock = Input()
                reset = Input()
                x     = Output(8)
                r     = Register(8)

                @comb
                def drive():
                    x = r

                @always(posedge(clock))
                def seq():
                    if reset:
                        r = 0
                    else:
                        r = r + 1

                @assert_always(clock)
                def r_lt_200():
                    return r < 200
        """)
        path = os.path.join(d, "top.py")
        with open(path, "w") as f:
            f.write(src)
        return path

    def test_synth_strips_formal_props(self):
        import argparse
        from veripy.cli import cmd_build
        with tempfile.TemporaryDirectory() as d:
            mod_path = self._make_module_file(d)
            toml_path = os.path.join(d, "veripy.toml")
            with open(toml_path, "w") as f:
                f.write(textwrap.dedent(f"""\
                    [build]
                    top = "top.py"
                    output = "rtl/"

                    [build.targets.synth]
                    synth = true
                    output = "synth/"
                """))
            out_dir = os.path.join(d, "synth")
            args = argparse.Namespace(
                file=None, output=out_dir, module=None, param=None, target="synth"
            )
            old = os.getcwd()
            os.chdir(d)
            try:
                cmd_build(args)
            finally:
                os.chdir(old)
            v_path = os.path.join(out_dir, "top.v")
            with open(v_path) as f:
                verilog = f.read()
            self.assertNotIn("assert", verilog)

    def test_no_synth_keeps_formal_props(self):
        import argparse
        from veripy.cli import cmd_build
        with tempfile.TemporaryDirectory() as d:
            mod_path = self._make_module_file(d)
            toml_path = os.path.join(d, "veripy.toml")
            with open(toml_path, "w") as f:
                f.write(textwrap.dedent(f"""\
                    [build]
                    top = "top.py"
                    output = "rtl/"

                    [build.targets.sim]
                    output = "sim/"
                """))
            out_dir = os.path.join(d, "sim")
            args = argparse.Namespace(
                file=None, output=out_dir, module=None, param=None, target="sim"
            )
            old = os.getcwd()
            os.chdir(d)
            try:
                cmd_build(args)
            finally:
                os.chdir(old)
            v_path = os.path.join(out_dir, "top.v")
            with open(v_path) as f:
                verilog = f.read()
            self.assertIn("assert", verilog)


if __name__ == "__main__":
    unittest.main()
