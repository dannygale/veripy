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
from .emit_verilog import _to_snake


def _load_modules(path, module_name=None, params=None):
    """Import a Python file and return [(name, instance)] of Module subclasses or @module factories."""
    parent_dir = os.path.dirname(os.path.abspath(path))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    spec = importlib.util.spec_from_file_location("_user_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    params = params or {}
    results = []

    # Class-based modules
    for name, cls in inspect.getmembers(mod, inspect.isclass):
        if not (issubclass(cls, Module) and cls is not Module):
            continue
        if module_name and name != module_name:
            continue
        sig = inspect.signature(cls.__init__)
        kwargs = {pname: int(params[pname]) for pname in sig.parameters
                  if pname != 'self' and pname in params}
        results.append((name, cls(**kwargs)))

    # @module factory functions
    for name, obj in vars(mod).items():
        if not getattr(obj, '_is_veripy_module', False):
            continue
        if module_name and name != module_name:
            continue
        kwargs = {k: int(params[k]) for k in obj._param_names if k in params}
        results.append((name, obj(**kwargs)))

    if module_name and not results:
        sys.exit(f"error: no module named '{module_name}' in {path}")
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


def cmd_init(args):
    """Scaffold a new VeriPy project with veripy.toml and directory structure."""
    import textwrap
    name = args.name
    dest = os.path.abspath(args.output or name)

    toml_path = os.path.join(dest, "veripy.toml")
    if os.path.exists(toml_path):
        sys.exit(f"error: {toml_path} already exists")

    os.makedirs(os.path.join(dest, "src"), exist_ok=True)
    os.makedirs(os.path.join(dest, "tests"), exist_ok=True)
    os.makedirs(os.path.join(dest, "rtl"), exist_ok=True)

    with open(toml_path, "w") as f:
        f.write(textwrap.dedent(f"""\
            [project]
            name = "{name}"
            version = "0.1.0"

            [build]
            top = "src/top.py"
            output = "rtl/"

            [test]
            path = "tests/"
        """))

    top_py = os.path.join(dest, "src", "top.py")
    with open(top_py, "w") as f:
        f.write(textwrap.dedent(f"""\
            from veripy import module, Input, Output, Register, posedge
            from veripy.context import comb, always


            @module
            def top(width=8):
                clock  = Input()
                reset  = Input()
                enable = Input()
                count  = Output(width)
                cnt    = Register(width)

                @comb
                def drive():
                    count = cnt

                @always(posedge(clock))
                def seq():
                    if reset:
                        cnt = 0
                    elif enable:
                        cnt = cnt + 1
        """))

    print(f"Initialized VeriPy project '{name}' in {dest}/")
    print(f"  {os.path.relpath(toml_path, dest)}")
    print(f"  src/top.py")
    print(f"  tests/")
    print(f"  rtl/")
    print(f"\nNext: veripy build  (from {dest}/)")


def cmd_build(args):
    # If no file given, try loading from veripy.toml
    if not args.file:
        from .config import load_config, get_target
        cfg = load_config()
        if cfg is None:
            sys.exit("error: no file given and no veripy.toml found")
        build = cfg["build"]
        if getattr(args, 'target', None):
            build = get_target(cfg, args.target)
        if not build["top"]:
            sys.exit("error: [build] top is not set in veripy.toml")
        # Resolve relative to config dir
        args.file = os.path.join(cfg["_dir"], build["top"])
        if not args.output:
            args.output = os.path.join(cfg["_dir"], build["output"])
        if not args.module and build["module"]:
            args.module = build["module"]
        if not args.param and build["params"]:
            args.param = [f"{k}={v}" for k, v in build["params"].items()]
        args._synth = build.get("synth", False)
    else:
        args._synth = False

    params = _parse_params(args.param)
    modules = _load_modules(args.file, module_name=args.module, params=params)
    if not modules:
        sys.exit(f"error: no modules found in {args.file}")

    all_modules = []  # [(snake_name, verilog_src)]
    seen = set()

    def _collect(mod, mod_name):
        if mod_name in seen:
            return
        seen.add(mod_name)
        for sub_name, sub in mod._submodules().items():
            if getattr(sub, '_is_blackbox', False):
                continue
            sub_snake = _to_snake(type(sub).__name__)
            factory = getattr(type(sub), '_veripy_factory', None)
            fresh = factory() if factory else type(sub)()
            _collect(fresh, sub_snake)
        from .lower import lower_module
        from .backend_verilog import emit_verilog as _emit_v
        ir = lower_module(mod, mod_name)
        if args._synth:
            ir.formal_props.clear()
        all_modules.append((mod_name, _emit_v(ir)))

    for name, instance in modules:
        _collect(instance, _to_snake(name))

    # Compile-check all files together
    combined = '\n\n'.join(src for _, src in all_modules)
    ok, stderr = _compile_verilog(combined)
    if not ok:
        print("error: iverilog compilation failed:", file=sys.stderr)
        print(stderr, file=sys.stderr)
        sys.exit(1)

    header = f"// Auto-generated by veripy from {os.path.basename(args.file)} — do not edit\n"

    if args.output:
        os.makedirs(args.output, exist_ok=True)
        for mod_name, src in all_modules:
            out_path = os.path.join(args.output, f"{mod_name}.v")
            with open(out_path, "w") as f:
                f.write(header + src)
            print(f"  {out_path}")
    else:
        print(header + combined)


def cmd_test(args):
    test_path = args.path
    if not test_path:
        from .config import load_config
        cfg = load_config()
        if cfg is not None:
            test_path = os.path.join(cfg["_dir"], cfg["test"]["path"])

    argv = ["python", "-m", "unittest"]
    if test_path:
        argv += ["discover", "-s", test_path, "-p", "test_*.py"]
    else:
        argv += ["discover", "-p", "test_*.py"]
    if args.verbose:
        argv.append("-v")
    sys.exit(subprocess.run(argv).returncode)


def cmd_import(args):
    import os
    path = args.file
    if os.path.isdir(path):
        from .import_verilog import import_project
        if not args.output:
            print("Error: -o <output_dir> is required when importing a directory", file=sys.stderr)
            sys.exit(1)
        result = import_project(path)
        for rel_py, source in sorted(result.items()):
            out_path = os.path.join(args.output, rel_py)
            os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
            with open(out_path, 'w') as f:
                f.write(source)
            print(f"Wrote {out_path}")
        # Write __init__.py files for each directory
        for root, dirs, _files in os.walk(args.output):
            init = os.path.join(root, '__init__.py')
            if not os.path.exists(init):
                open(init, 'w').close()
    else:
        from .import_verilog import import_verilog
        with open(path) as f:
            src = f.read()
        python_src = import_verilog(src)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(python_src)
            print(f"Wrote {args.output}")
        else:
            print(python_src)


def cmd_lint(args):
    from .lint import lint
    if not args.file:
        from .config import load_config
        cfg = load_config()
        if cfg is None:
            sys.exit("error: no file given and no veripy.toml found")
        build = cfg["build"]
        if not build["top"]:
            sys.exit("error: [build] top is not set in veripy.toml")
        args.file = os.path.join(cfg["_dir"], build["top"])
        if not args.module and build["module"]:
            args.module = build["module"]
    modules = _load_modules(args.file, module_name=args.module)
    if not modules:
        sys.exit(f"error: no modules found in {args.file}")
    found = False
    for name, instance in modules:
        warnings = lint(instance)
        if warnings:
            found = True
            for level, msg in warnings:
                print(f"{name}: [{level}] {msg}")
    if not found:
        print("No issues found.")
    sys.exit(1 if found else 0)


def cmd_formal(args):
    """Generate .sby + formal Verilog for SymbiYosys verification."""
    from .lower import lower_module
    from .backend_verilog import emit_verilog
    from .backend_formal import emit_sby

    params = _parse_params(args.param)
    modules = _load_modules(args.file, module_name=args.module, params=params)
    if not modules:
        sys.exit(f"error: no modules found in {args.file}")

    out_dir = args.output or '.'
    os.makedirs(out_dir, exist_ok=True)

    for name, instance in modules:
        mod_name = _to_snake(name)
        ir = lower_module(instance, mod_name)
        if not ir.formal_props:
            print(f"  {mod_name}: no formal properties, skipping")
            continue

        v_src = emit_verilog(ir)
        sby_src = emit_sby(ir, depth=args.depth)

        v_path = os.path.join(out_dir, f'{mod_name}.v')
        sby_path = os.path.join(out_dir, f'{mod_name}.sby')
        with open(v_path, 'w') as f:
            f.write(v_src)
        with open(sby_path, 'w') as f:
            f.write(sby_src)
        print(f"  {v_path}")
        print(f"  {sby_path}")

        n_assert = sum(1 for p in ir.formal_props if p.kind == 'assert')
        n_cover = sum(1 for p in ir.formal_props if p.kind == 'cover')
        n_assume = sum(1 for p in ir.formal_props if p.kind == 'assume')
        parts = []
        if n_assert: parts.append(f'{n_assert} assert')
        if n_cover: parts.append(f'{n_cover} cover')
        if n_assume: parts.append(f'{n_assume} assume')
        print(f"  {mod_name}: {', '.join(parts)}")


def cmd_equiv(args):
    """Generate Yosys equivalence checking script for two modules."""
    from .lower import lower_module
    from .backend_verilog import emit_verilog
    from .backend_equiv import emit_equiv_script

    gold_params = _parse_params(args.gold_param)
    gate_params = _parse_params(args.gate_param)

    gold_mods = _load_modules(args.gold, module_name=args.gold_module, params=gold_params)
    gate_mods = _load_modules(args.gate, module_name=args.gate_module, params=gate_params)
    if not gold_mods:
        sys.exit(f"error: no modules found in {args.gold}")
    if not gate_mods:
        sys.exit(f"error: no modules found in {args.gate}")

    gold_name, gold_inst = gold_mods[0]
    gate_name, gate_inst = gate_mods[0]

    gold_ir = lower_module(gold_inst, _to_snake(gold_name))
    gate_ir = lower_module(gate_inst, _to_snake(gate_name))

    out_dir = args.output or '.'
    os.makedirs(out_dir, exist_ok=True)

    gold_v = emit_verilog(gold_ir)
    gate_v = emit_verilog(gate_ir)
    script = emit_equiv_script(gold_ir, gate_ir)

    gold_path = os.path.join(out_dir, 'gold.v')
    gate_path = os.path.join(out_dir, 'gate.v')
    script_path = os.path.join(out_dir, 'equiv.ys')

    for path, content in [(gold_path, gold_v), (gate_path, gate_v), (script_path, script)]:
        with open(path, 'w') as f:
            f.write(content)
        print(f"  {path}")

    if args.run:
        result = subprocess.run(['yosys', '-s', script_path],
                                capture_output=True, text=True, cwd=out_dir)
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(1)
        print("Equivalence check PASSED")
    else:
        print(f"\nRun: yosys -s {script_path}")


def cmd_doc(args):
    """Generate markdown documentation for modules in a Python file."""
    from .autodoc import to_markdown
    params = _parse_params(args.param)
    modules = _load_modules(args.file, module_name=args.module, params=params)
    if not modules:
        sys.exit(f"error: no modules found in {args.file}")
    for name, instance in modules:
        md = to_markdown(instance, module_name=_to_snake(name))
        if args.output:
            os.makedirs(args.output, exist_ok=True)
            out_path = os.path.join(args.output, f"{_to_snake(name)}.md")
            with open(out_path, "w") as f:
                f.write(md)
            print(f"  {out_path}")
        else:
            print(md)


def cmd_profile(args):
    """Run test case(s) and compare Python sim vs iverilog performance."""
    import time
    from .verify import VeripyTestCase

    spec = importlib.util.spec_from_file_location("_profile_mod", args.file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_profile_mod"] = mod
    spec.loader.exec_module(mod)

    cases = []
    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, VeripyTestCase) and obj is not VeripyTestCase:
            cases.append((name, obj))
    if not cases:
        sys.exit(f"error: no VeripyTestCase subclasses found in {args.file}")

    rows = []
    for cls_name, cls in cases:
        methods = [m for m in dir(cls) if m.startswith('test')]
        if args.test:
            methods = [m for m in methods if m == args.test or m == f'test_{args.test}']
        for method_name in sorted(methods):
            label = f'{cls_name}.{method_name}'
            tc = cls(method_name)

            # Python sim
            tc._begin()
            tc._ran_sim = False
            t0 = time.perf_counter()
            getattr(tc, method_name)()
            if not tc._ran_sim:
                tc.run_sim()
            py_time = time.perf_counter() - t0

            # iverilog
            t0 = time.perf_counter()
            tc._run_iverilog()
            iv_time = time.perf_counter() - t0

            # verify correctness
            try:
                tc._assert_traces_match()
                status = ''
            except AssertionError as e:
                status = ' MISMATCH'

            ratio = py_time / iv_time if iv_time > 0.001 else float('inf')
            winner = 'python' if py_time < iv_time else 'iverilog'
            rows.append((label, py_time, iv_time, ratio, winner, status))

    if not rows:
        sys.exit("error: no matching test methods found")

    w = max(len(r[0]) for r in rows)
    print(f"\n{'Test':<{w}}    Python    iverilog   Ratio  Winner")
    print('-' * (w + 45))
    for label, py_t, iv_t, ratio, winner, status in rows:
        print(f'{label:<{w}}  {py_t:8.4f}s  {iv_t:8.4f}s  {ratio:5.1f}x  {winner}{status}')
    print()


def cmd_fpga(args):
    """FPGA subcommands: build, program, and boards."""
    if args.fpga_command == 'boards':
        from .fpga import BOARDS
        print(f"  {'Name':<12} {'Family':<8} {'Part':<22} {'Package'}")
        print("  " + "-" * 56)
        for name, board in sorted(BOARDS.items()):
            print(f"  {board.name:<12} {board.family:<8} {board.part:<22} {board.package}")
        return

    if args.fpga_command == 'build':
        from .fpga import get_board, ConstraintSet
        from .backend_fpga import synthesize
        from .lower import lower_module
        from .backend_verilog import emit_verilog
        from .flatten import flatten_ir
        from .emit_verilog import _to_snake

        # Resolve board
        board_name = args.board
        if not board_name:
            from .config import load_config
            cfg = load_config()
            if cfg:
                board_name = cfg.get("fpga", {}).get("board", "")
        if not board_name:
            sys.exit("error: --board <name> is required (or set [fpga] board in veripy.toml)")

        try:
            board = get_board(board_name)
        except KeyError as e:
            sys.exit(f"error: {e}")

        # Resolve source file
        src_file = args.file
        if not src_file:
            from .config import load_config
            cfg = load_config()
            if cfg is None:
                sys.exit("error: no file given and no veripy.toml found")
            build = cfg["build"]
            if not build["top"]:
                sys.exit("error: [build] top is not set in veripy.toml")
            src_file = os.path.join(cfg["_dir"], build["top"])

        params = _parse_params(args.param)
        modules = _load_modules(src_file, module_name=args.module, params=params)
        if not modules:
            sys.exit(f"error: no modules found in {src_file}")

        top_name, top_inst = modules[0]
        top_snake = _to_snake(top_name)

        ir = lower_module(top_inst, top_snake)
        verilog_src = emit_verilog(ir)

        # Build port map from --map port=BOARD_PIN args
        port_map = {}
        for mapping in (args.map or []):
            if "=" not in mapping:
                sys.exit(f"error: bad --map format '{mapping}', expected port=BOARD_PIN")
            port, pin_name = mapping.split("=", 1)
            port_map[port] = pin_name

        # Also read from veripy.toml [fpga.pins]
        try:
            from .config import load_config
            cfg = load_config()
            if cfg:
                toml_pins = cfg.get("fpga", {}).get("pins", {})
                for port, pin_name in toml_pins.items():
                    if port not in port_map:
                        port_map[port] = pin_name
        except Exception:
            pass

        if not port_map:
            sys.exit(
                "error: no pin mappings provided. Use --map port=BOARD_PIN "
                "or set [fpga.pins] in veripy.toml"
            )

        try:
            cs = board.constraint_set(port_map)
        except KeyError as e:
            sys.exit(f"error: unknown board pin {e}. "
                     f"Run 'veripy fpga boards' to see available pins.")

        out_dir = args.output or "build"
        bitstream = synthesize(verilog_src, board, cs, top_snake, out_dir)
        print(f"Bitstream: {bitstream}")

        if getattr(args, 'program', False):
            from .backend_fpga import program
            program(bitstream, board, programmer=getattr(args, 'programmer', None))

    if args.fpga_command == 'program':
        from .fpga import get_board
        from .backend_fpga import program as _program

        board_name = args.board
        if not board_name:
            from .config import load_config
            cfg = load_config()
            if cfg:
                board_name = cfg.get("fpga", {}).get("board", "")
        if not board_name:
            sys.exit("error: --board <name> is required (or set [fpga] board in veripy.toml)")

        try:
            board = get_board(board_name)
        except KeyError as e:
            sys.exit(f"error: {e}")

        _program(args.bitstream, board, programmer=args.programmer)


def cmd_soc(args):
    """SoC builder subcommands."""
    if args.soc_command == 'build':
        from .soc import parse_soc_config, AddressMap, build_soc
        try:
            build_soc(args.config, args.output or '.')
        except (ValueError, FileNotFoundError) as e:
            sys.exit(f"error: {e}")


def cmd_ip(args):
    """Manage installed VeriPy IP packages."""
    from .packaging import discover, scaffold

    if args.ip_command == 'list':
        packages = discover()
        if not packages:
            print("No VeriPy IP packages installed.")
            print("Create one with: veripy ip init <name>")
            return
        for pkg in packages:
            print(f"  {pkg.name:20s} {pkg.version:10s} {pkg.description}")

    elif args.ip_command == 'show':
        packages = discover()
        match = [p for p in packages if p.name == args.name]
        if not match:
            sys.exit(f"error: IP package '{args.name}' not found")
        pkg = match[0]
        print(f"Name:        {pkg.name}")
        print(f"Version:     {pkg.version}")
        print(f"Module:      {pkg.module_path}")
        print(f"Description: {pkg.description}")

    elif args.ip_command == 'init':
        path = scaffold(args.name, output_dir=args.output or '.')
        print(f"Created IP package skeleton at {path}/")
        print(f"  {path}/pyproject.toml")
        print(f"  {path}/veripy_{args.name}/__init__.py")
        print(f"\nTo install for development: pip install -e {path}")


def main():
    parser = argparse.ArgumentParser(prog="veripy", description="VeriPy HDL toolchain")
    sub = parser.add_subparsers(dest="command", required=True)

    # build
    p_build = sub.add_parser("build", help="Emit and compile-check Verilog from a Python module")
    p_build.add_argument("file", nargs="?", help="Python file containing Module subclass(es) (default: from veripy.toml)")
    p_build.add_argument("-o", "--output", help="Output directory for .v files (default: stdout)")
    p_build.add_argument("-m", "--module", help="Target a specific Module subclass by name")
    p_build.add_argument("-p", "--param", action="append", help="Module parameter (e.g. -p n=4)")
    p_build.add_argument("--target", help="Build target name from [build.targets.*] in veripy.toml")

    # test
    p_test = sub.add_parser("test", help="Run dual-path VeripyTestCase suite")
    p_test.add_argument("path", nargs="?", help="Test file or directory (default: discover)")
    p_test.add_argument("-v", "--verbose", action="store_true")

    # import
    p_import = sub.add_parser("import", help="Convert Verilog to VeriPy Python")
    p_import.add_argument("file", help="Verilog file or directory to convert")
    p_import.add_argument("-o", "--output", help="Output file (single .v) or directory (project import)")

    # lint
    p_lint = sub.add_parser("lint", help="Run static checks on a VeriPy module")
    p_lint.add_argument("file", nargs="?", help="Python file containing Module subclass(es) (default: from veripy.toml)")
    p_lint.add_argument("-m", "--module", help="Target a specific Module subclass by name")

    # formal
    p_formal = sub.add_parser("formal", help="Generate .sby + Verilog for SymbiYosys formal verification")
    p_formal.add_argument("file", help="Python file containing Module subclass(es)")
    p_formal.add_argument("-o", "--output", help="Output directory (default: current dir)")
    p_formal.add_argument("-m", "--module", help="Target a specific Module subclass by name")
    p_formal.add_argument("-p", "--param", action="append", help="Module parameter (e.g. -p n=4)")
    p_formal.add_argument("-d", "--depth", type=int, default=20, help="BMC depth (default: 20)")

    # profile
    p_prof = sub.add_parser("profile", help="Compare Python sim vs iverilog performance")
    p_prof.add_argument("file", help="Test file containing VeripyTestCase subclass(es)")
    p_prof.add_argument("-t", "--test", help="Run only this test method (e.g. test_nop)")

    # doc
    p_doc = sub.add_parser("doc", help="Generate markdown documentation from module definitions")
    p_doc.add_argument("file", help="Python file containing Module subclass(es)")
    p_doc.add_argument("-o", "--output", help="Output directory for .md files (default: stdout)")
    p_doc.add_argument("-m", "--module", help="Target a specific Module subclass by name")
    p_doc.add_argument("-p", "--param", action="append", help="Module parameter (e.g. -p n=4)")

    # equiv
    p_equiv = sub.add_parser("equiv", help="Formal equivalence checking between two modules")
    p_equiv.add_argument("gold", help="Gold (reference) Python file")
    p_equiv.add_argument("gate", help="Gate (implementation) Python file")
    p_equiv.add_argument("-o", "--output", help="Output directory (default: current dir)")
    p_equiv.add_argument("--gold-module", help="Module name in gold file")
    p_equiv.add_argument("--gate-module", help="Module name in gate file")
    p_equiv.add_argument("--gold-param", action="append", default=[], help="Gold module param (e.g. --gold-param n=4)")
    p_equiv.add_argument("--gate-param", action="append", default=[], help="Gate module param (e.g. --gate-param n=4)")
    p_equiv.add_argument("--run", action="store_true", help="Run Yosys automatically (requires yosys on PATH)")

    # fpga
    p_fpga = sub.add_parser("fpga", help="FPGA build flow")
    fpga_sub = p_fpga.add_subparsers(dest="fpga_command", required=True)
    fpga_sub.add_parser("boards", help="List built-in board definitions")
    p_fpga_build = fpga_sub.add_parser("build", help="Synthesize to FPGA bitstream")
    p_fpga_build.add_argument("file", nargs="?", help="Python file (default: from veripy.toml)")
    p_fpga_build.add_argument("--board", help="Target board name (e.g. icebreaker, ulx3s, arty)")
    p_fpga_build.add_argument("-m", "--module", help="Target a specific module by name")
    p_fpga_build.add_argument("-p", "--param", action="append", help="Module parameter (e.g. -p n=4)")
    p_fpga_build.add_argument("--map", action="append", metavar="PORT=BOARD_PIN",
                               help="Map module port to board pin (e.g. --map clk=CLK)")
    p_fpga_build.add_argument("-o", "--output", help="Output directory (default: build/)")
    p_fpga_build.add_argument("--program", action="store_true",
                               help="Program bitstream onto FPGA after synthesis")
    p_fpga_build.add_argument("--programmer", choices=["iceprog", "openFPGALoader"],
                               help="Programmer to use (default: auto-detected from board family)")
    p_fpga_program = fpga_sub.add_parser("program", help="Program a bitstream onto an FPGA")
    p_fpga_program.add_argument("bitstream", help="Bitstream file to program")
    p_fpga_program.add_argument("--board", help="Target board name (e.g. icebreaker, ulx3s, arty)")
    p_fpga_program.add_argument("--programmer", choices=["iceprog", "openFPGALoader"],
                                 help="Programmer to use (default: auto-detected from board family)")

    # soc
    p_soc = sub.add_parser("soc", help="SoC builder commands")
    soc_sub = p_soc.add_subparsers(dest="soc_command", required=True)
    p_soc_build = soc_sub.add_parser("build", help="Build SoC from YAML/JSON config")
    p_soc_build.add_argument("config", help="SoC config file (.yaml or .json)")
    p_soc_build.add_argument("-o", "--output", help="Output directory (default: current dir)")

    # ip
    p_ip = sub.add_parser("ip", help="Manage VeriPy IP packages")
    ip_sub = p_ip.add_subparsers(dest="ip_command", required=True)
    ip_sub.add_parser("list", help="List installed VeriPy IP packages")
    p_ip_show = ip_sub.add_parser("show", help="Show details of an installed IP package")
    p_ip_show.add_argument("name", help="IP package name")
    p_ip_init = ip_sub.add_parser("init", help="Scaffold a new VeriPy IP package")
    p_ip_init.add_argument("name", help="IP package name (e.g. 'axi' creates veripy-axi)")
    p_ip_init.add_argument("-o", "--output", help="Output directory (default: current dir)")

    # init
    p_init = sub.add_parser("init", help="Scaffold a new VeriPy project with veripy.toml")
    p_init.add_argument("name", help="Project name")
    p_init.add_argument("-o", "--output", help="Output directory (default: ./<name>)")

    args = parser.parse_args()
    {
        "build": cmd_build, "test": cmd_test, "import": cmd_import,
        "lint": cmd_lint, "formal": cmd_formal, "profile": cmd_profile,
        "equiv": cmd_equiv, "doc": cmd_doc, "ip": cmd_ip,
        "init": cmd_init, "soc": cmd_soc, "fpga": cmd_fpga,
    }[args.command](args)


if __name__ == "__main__":
    main()
