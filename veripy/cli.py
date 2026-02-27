"""veripy CLI — build Verilog from Python modules, run dual-path tests."""

import argparse
import importlib.util
import inspect
import os
import subprocess
import sys
import tempfile
import unittest

import re

from .module import Module
from .emit_verilog import VerilogEmitter


def _to_snake(name):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).lower()


def _load_modules(path, module_name=None, params=None):
    """Import a Python file and return [(name, instance)] of Module subclasses."""
    spec = importlib.util.spec_from_file_location("_user_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    classes = [
        (name, cls) for name, cls in inspect.getmembers(mod, inspect.isclass)
        if issubclass(cls, Module) and cls is not Module
    ]
    if module_name:
        classes = [(n, c) for n, c in classes if n == module_name]
        if not classes:
            sys.exit(f"error: no Module subclass named '{module_name}' in {path}")

    params = params or {}
    results = []
    for name, cls in classes:
        sig = inspect.signature(cls.__init__)
        kwargs = {}
        for pname, param in sig.parameters.items():
            if pname == 'self':
                continue
            if pname in params:
                kwargs[pname] = int(params[pname])
        results.append((name, cls(**kwargs)))
    return results


def _compile_verilog(verilog_src):
    """Compile Verilog source with iverilog. Returns (ok, stderr)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_f = os.path.join(tmpdir, "check.v")
        out_f = os.path.join(tmpdir, "check.out")
        with open(src_f, "w") as f:
            f.write(verilog_src)
        r = subprocess.run(
            ["iverilog", "-o", out_f, src_f],
            capture_output=True, text=True,
        )
        return r.returncode == 0, r.stderr


def _parse_params(raw):
    """Parse ['n=4', 'w=8'] into {'n': '4', 'w': '8'}."""
    params = {}
    for item in (raw or []):
        if "=" not in item:
            sys.exit(f"error: bad --param format '{item}', expected key=value")
        k, v = item.split("=", 1)
        params[k] = v
    return params


def cmd_build(args):
    params = _parse_params(args.param)
    modules = _load_modules(args.file, module_name=args.module, params=params)
    if not modules:
        sys.exit(f"error: no Module subclasses found in {args.file}")

    for name, instance in modules:
        snake = _to_snake(name)
        emitter = VerilogEmitter(instance, snake)
        src = emitter.emit_all() if instance._submodules() else emitter.emit()

        ok, stderr = _compile_verilog(src)
        if not ok:
            print(f"error: iverilog compilation failed for {name}:", file=sys.stderr)
            print(stderr, file=sys.stderr)
            sys.exit(1)

        if args.output:
            os.makedirs(args.output, exist_ok=True)
            out_path = os.path.join(args.output, f"{snake}.v")
            with open(out_path, "w") as f:
                f.write(src)
            print(f"{name}: {out_path}")
        else:
            print(src)


def cmd_test(args):
    argv = ["python", "-m", "unittest"]
    if args.path:
        argv += ["discover", "-s", args.path, "-p", "test_*.py"]
    else:
        argv += ["discover", "-p", "test_*.py"]
    if args.verbose:
        argv.append("-v")
    sys.exit(subprocess.run(argv).returncode)


def main():
    parser = argparse.ArgumentParser(prog="veripy", description="VeriPy HDL toolchain")
    sub = parser.add_subparsers(dest="command", required=True)

    # build
    p_build = sub.add_parser("build", help="Emit and compile-check Verilog from a Python module")
    p_build.add_argument("file", help="Python file containing Module subclass(es)")
    p_build.add_argument("-o", "--output", help="Output directory for .v files (default: stdout)")
    p_build.add_argument("-m", "--module", help="Target a specific Module subclass by name")
    p_build.add_argument("-p", "--param", action="append", help="Module parameter (e.g. -p n=4)")

    # test
    p_test = sub.add_parser("test", help="Run dual-path VeripyTestCase suite")
    p_test.add_argument("path", nargs="?", help="Test file or directory (default: discover)")
    p_test.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    {"build": cmd_build, "test": cmd_test}[args.command](args)


if __name__ == "__main__":
    main()
