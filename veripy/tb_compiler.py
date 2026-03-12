"""Compile TestBench generators to Cython with direct C model calls.

Pipeline:
  1. rewrite_generator() — AST-rewrite signal access to veripy_set_*/veripy_get_*
  2. emit_generators_pyx() — generate .pyx with cdef extern + rewritten generators
  3. compile_generators() — cythonize + cc → cached .so, return compiled functions
"""

import ast
import inspect
import textwrap


# ── AST rewriter ─────────────────────────────────────────────────────

class _GeneratorRewriter(ast.NodeTransformer):
    """Rewrite a test generator for Cython compilation.

    Transforms:
      dut.x = v              → veripy_set_x(_ptr, v)
      dut.x  (read)          → veripy_get_x(_ptr)
      self.get('x')          → veripy_get_x(_ptr)
      self.set(x=v, ...)     → veripy_set_x(_ptr, v); ...
      self.assertEqual(a, b) → assert (a) == (b)
      self.assertNotEqual     → assert (a) != (b)
      self.assertTrue(a)     → assert (a)
      self.assertFalse(a)    → assert not (a)
      self.assertGreater(a,b)→ assert (a) > (b)
      self.assertIn(a, b)    → assert (a) in (b)
      self.assertLessEqual    → assert (a) <= (b)
      self.run_cycles(n)     → _engine.run_cycles(n)
      self._engine.X         → _engine.X
      self.fork(...)         → _engine.fork(...)
      self.fork_any(...)     → _engine.fork_any(...)
      self.fail(msg)         → raise AssertionError(msg)
      <closure_var>          → constant value (if int/float)

    Only rewrites the top-level function def, not nested functions.
    """

    _ASSERT_OPS = {
        'assertEqual':    (ast.Eq, False),
        'assertNotEqual': (ast.NotEq, False),
        'assertGreater':  (ast.Gt, False),
        'assertLess':     (ast.Lt, False),
        'assertGreaterEqual': (ast.GtE, False),
        'assertLessEqual':    (ast.LtE, False),
    }

    def __init__(self, input_names, all_names, dut_alias='dut', tc_alias='self',
                 func=None):
        self.input_names = set(input_names)
        self.all_names = set(all_names)
        self.dut_alias = dut_alias
        self.tc_alias = tc_alias
        self._func = func
        self._depth = 0  # track function nesting

    # ── helpers ───────────────────────────────────────────────────

    def _is_dut_attr(self, node):
        """Return signal name if node is dut.signal, else None."""
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == self.dut_alias
                and node.attr in self.all_names):
            return node.attr
        return None

    def _is_tc_call(self, node, method):
        """Check if node is self.<method>(...)."""
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == method
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == self.tc_alias)

    def _is_tc_attr(self, node, attr):
        """Check if node is self.<attr>."""
        return (isinstance(node, ast.Attribute)
                and node.attr == attr
                and isinstance(node.value, ast.Name)
                and node.value.id == self.tc_alias)

    def _make_call(self, func_name, args):
        return ast.Call(
            func=ast.Name(id=func_name, ctx=ast.Load()),
            args=args, keywords=[],
        )

    def _make_get(self, sig_name):
        return self._make_call(f'veripy_get_{sig_name}', [
            ast.Name(id='_ptr', ctx=ast.Load()),
        ])

    def _make_set_stmt(self, sig_name, value_node):
        return ast.Expr(value=self._make_call(f'veripy_set_{sig_name}', [
            ast.Name(id='_ptr', ctx=ast.Load()),
            value_node,
        ]))

    # ── visitors ──────────────────────────────────────────────────

    def visit_FunctionDef(self, node):
        """Rewrite only the top-level function signature."""
        if self._depth == 0:
            self._depth += 1
            self.generic_visit(node)
            self._depth -= 1
            node.args = ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg='_ptr_val'), ast.arg(arg='_engine')],
                vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
            )
            node.decorator_list = []
            return node
        else:
            # Nested function — don't rewrite signature, but still visit body
            self._depth += 1
            self.generic_visit(node)
            self._depth -= 1
            return node

    def visit_Assign(self, node):
        """Rewrite dut.signal = value → veripy_set_signal(_ptr, value)."""
        self.generic_visit(node)
        if len(node.targets) == 1:
            sig = self._is_dut_attr(node.targets[0])
            if sig and sig in self.input_names:
                return self._make_set_stmt(sig, node.value)
        return node

    def visit_Expr(self, node):
        """Rewrite self.set/assertEqual/run_cycles/fork/fail etc."""
        self.generic_visit(node)
        call = node.value
        if not isinstance(call, ast.Call):
            return node

        # self.set(x=v, y=w) → veripy_set_x(_ptr, v); veripy_set_y(_ptr, w)
        if self._is_tc_call(call, 'set'):
            stmts = []
            for kw in call.keywords:
                if kw.arg and kw.arg in self.input_names:
                    stmts.append(self._make_set_stmt(kw.arg, kw.value))
            return stmts if stmts else node

        # self.assertEqual / assertNotEqual / assertGreater / etc.
        for method, (op_cls, _) in self._ASSERT_OPS.items():
            if self._is_tc_call(call, method) and len(call.args) >= 2:
                return ast.Assert(
                    test=ast.Compare(
                        left=call.args[0], ops=[op_cls()],
                        comparators=[call.args[1]],
                    ),
                    msg=call.args[2] if len(call.args) > 2 else None,
                )

        # self.assertTrue(a) → assert (a)
        if self._is_tc_call(call, 'assertTrue') and call.args:
            return ast.Assert(test=call.args[0], msg=None)

        # self.assertFalse(a) → assert not (a)
        if self._is_tc_call(call, 'assertFalse') and call.args:
            return ast.Assert(
                test=ast.UnaryOp(op=ast.Not(), operand=call.args[0]),
                msg=None,
            )

        # self.assertIn(a, b) → assert (a) in (b)
        if self._is_tc_call(call, 'assertIn') and len(call.args) >= 2:
            return ast.Assert(
                test=ast.Compare(
                    left=call.args[0], ops=[ast.In()],
                    comparators=[call.args[1]],
                ),
                msg=call.args[2] if len(call.args) > 2 else None,
            )

        # self.fail(msg) → raise AssertionError(msg)
        if self._is_tc_call(call, 'fail'):
            msg = call.args[0] if call.args else ast.Constant(value='fail')
            return ast.Raise(
                exc=ast.Call(
                    func=ast.Name(id='AssertionError', ctx=ast.Load()),
                    args=[msg], keywords=[],
                ),
                cause=None,
            )

        # self.run_cycles(n) → _engine.run_cycles(n)
        if self._is_tc_call(call, 'run_cycles'):
            call.func = ast.Attribute(
                value=ast.Name(id='_engine', ctx=ast.Load()),
                attr='run_cycles', ctx=ast.Load(),
            )
            return node

        # self.fork(...) / self.fork_any(...) → _engine.fork/fork_any(...)
        for method in ('fork', 'fork_any'):
            if self._is_tc_call(call, method):
                call.func = ast.Attribute(
                    value=ast.Name(id='_engine', ctx=ast.Load()),
                    attr=method, ctx=ast.Load(),
                )
                return node

        return node

    def visit_Attribute(self, node):
        """Rewrite dut.signal reads and self._engine → _engine."""
        self.generic_visit(node)
        # dut.signal → veripy_get_signal(_ptr)
        sig = self._is_dut_attr(node)
        if sig and isinstance(node.ctx, ast.Load):
            return self._make_get(sig)
        # self._engine → _engine
        if self._is_tc_attr(node, '_engine'):
            return ast.Name(id='_engine', ctx=node.ctx)
        return node

    def visit_Name(self, node):
        """Resolve closure/global constants to literal values."""
        if isinstance(node.ctx, ast.Load) and self._func is not None:
            val = self._resolve_name(node.id)
            if isinstance(val, (int, float)):
                return ast.Constant(value=val)
        return node

    def _resolve_name(self, name):
        """Resolve a bare name via closure/globals. Only returns int/float."""
        func = self._func
        if func is None:
            return None
        code = getattr(func, '__code__', None)
        if code and name in code.co_freevars and func.__closure__:
            idx = code.co_freevars.index(name)
            try:
                val = func.__closure__[idx].cell_contents
                if isinstance(val, (int, float)):
                    return val
            except ValueError:
                pass
        if hasattr(func, '__globals__') and name in func.__globals__:
            val = func.__globals__[name]
            if isinstance(val, (int, float)):
                return val
        return None

    def visit_Call(self, node):
        """Rewrite self.get('x') → veripy_get_x(_ptr)."""
        self.generic_visit(node)
        if (self._is_tc_call(node, 'get')
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in self.all_names):
            return self._make_get(node.args[0].value)
        return node


def _detect_dut_alias(func):
    """Detect the variable name used for self.dut in a generator function."""
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == 'dut'):
            return node.targets[0].id
    return None


def _detect_tc_alias(func):
    """Detect the TestBench variable name captured in a generator's closure."""
    code = getattr(func, '__code__', None)
    if code and 'self' in code.co_freevars:
        return 'self'
    if code and func.__closure__:
        for i, name in enumerate(code.co_freevars):
            try:
                val = func.__closure__[i].cell_contents
                if hasattr(val, '_tb_initial') and hasattr(val, 'get'):
                    return name
            except ValueError:
                pass
    return 'self'


def rewrite_generator(func, input_names, all_names, unique_suffix=''):
    """Rewrite a test generator function for Cython compilation.

    Returns (func_name, rewritten_source, closure_param_names).
    closure_param_names lists the extra parameters added for captured variables.
    """
    src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    func_def = tree.body[0]
    orig_name = func_def.name

    dut_alias = _detect_dut_alias(func) or 'dut'
    tc_alias = _detect_tc_alias(func)

    rewriter = _GeneratorRewriter(input_names, all_names, dut_alias, tc_alias,
                                  func=func)
    tree = rewriter.visit(tree)
    ast.fix_missing_locations(tree)

    # Collect closure variables that weren't inlined as constants
    closure_params = []
    code = getattr(func, '__code__', None)
    if code and func.__closure__:
        for i, name in enumerate(code.co_freevars):
            try:
                val = func.__closure__[i].cell_contents
            except ValueError:
                continue
            # Skip int/float — already inlined by visit_Name
            if isinstance(val, (int, float)):
                continue
            closure_params.append(name)

    # Also collect referenced globals that aren't builtins or constants
    _builtins = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
    _builtins |= {'veripy_set_', 'veripy_get_', 'until', 'Until', 'range', 'len',
                   'int', 'float', 'str', 'bool', 'list', 'dict', 'set', 'tuple',
                   'print', 'True', 'False', 'None', 'TimeoutError', 'AssertionError'}
    if code and hasattr(func, '__globals__'):
        for name in code.co_names:
            if name in closure_params:
                continue
            if name.startswith('veripy_'):
                continue
            if name in _builtins:
                continue
            if name in func.__globals__:
                val = func.__globals__[name]
                if isinstance(val, (int, float)):
                    continue  # already inlined
                if isinstance(val, type) and name[0].isupper():
                    continue  # skip class references (likely imports)
                closure_params.append(name)

    # Add closure variables as extra function parameters
    func_def = tree.body[0]
    for name in closure_params:
        func_def.args.args.append(ast.arg(arg=name))
    ast.fix_missing_locations(tree)

    func_name = f'{orig_name}{unique_suffix}'
    tree.body[0].name = func_name

    return func_name, ast.unparse(tree), closure_params


# ── .pyx generator ───────────────────────────────────────────────────

def emit_generators_pyx(flat_ir, generator_sources, model_c_src=''):
    """Generate a .pyx file with cdef extern declarations and compiled generators.

    Args:
        flat_ir: IRModule with ports/regs for signal declarations
        generator_sources: list of (func_name, source_str) from rewrite_generator
        model_c_src: C model source for filtering wire getters

    Returns:
        .pyx source string
    """
    from .backend_csim import _resolve_width

    inputs = []
    all_getters = []
    seen = set()
    for p in flat_ir.ports:
        w = _resolve_width(p.width, flat_ir.params)
        if p.direction in ('input', 'inout'):
            inputs.append((p.name, w))
        all_getters.append((p.name, w))
        seen.add(p.name)
    for r in flat_ir.regs:
        if r.name not in seen:
            all_getters.append((r.name, _resolve_width(r.width, flat_ir.params)))
            seen.add(r.name)
    for w in flat_ir.wires:
        if w.name not in seen and (not model_c_src or f'veripy_get_{w.name}' in model_c_src):
            all_getters.append((w.name, _resolve_width(w.width, flat_ir.params)))
            seen.add(w.name)

    lines = [
        '# cython: language_level=3',
        'from libc.stdint cimport uint64_t, uintptr_t',
        'from veripy.sim import Until, until',
        '',
        'cdef extern from "model.h":',
        '    void  veripy_eval(void* p)',
    ]
    for name, _ in inputs:
        lines.append(f'    void     veripy_set_{name}(void* p, uint64_t v)')
    for name, _ in all_getters:
        lines.append(f'    uint64_t veripy_get_{name}(void* p)')
    for m in flat_ir.mems:
        lines.append(f'    uint64_t veripy_get_{m.name}(void* p, uint64_t idx)')
        lines.append(f'    void     veripy_set_{m.name}(void* p, uint64_t idx, uint64_t v)')
    lines.append('')

    for func_name, source in generator_sources:
        src_lines = source.split('\n')
        out_lines = []
        injected = False
        for line in src_lines:
            out_lines.append(line)
            if not injected and line.lstrip().startswith('def ') and line.rstrip().endswith(':'):
                out_lines.append('    cdef void* _ptr = <void*><uintptr_t>_ptr_val')
                injected = True
        lines.append('\n'.join(out_lines))
        lines.append('')

    return '\n'.join(lines) + '\n'


# ── Compiler ─────────────────────────────────────────────────────────

def compile_generators(flat_ir, generator_funcs, model_c_src, header_src):
    """Compile test generators to a Cython .so with direct C model calls.

    Returns dict mapping func_name → compiled generator function.
    """
    import hashlib
    import importlib.util
    import os
    import subprocess
    import sysconfig
    import tempfile

    from .backend_csim import _resolve_width, _default_model_cache_dir

    input_names = {p.name for p in flat_ir.ports if p.direction in ('input', 'inout')}
    # Only include wires that have getters in the C model
    accessible_wires = {w.name for w in flat_ir.wires
                        if not model_c_src or f'veripy_get_{w.name}' in model_c_src}
    all_names = {p.name for p in flat_ir.ports} | {r.name for r in flat_ir.regs} | accessible_wires

    gen_sources = []
    closure_info = []  # parallel list: [(param_names, param_values), ...]
    seen_names = {}
    for func in generator_funcs:
        base = func.__name__
        idx = seen_names.get(base, 0)
        seen_names[base] = idx + 1
        suffix = f'_{idx}' if idx > 0 or base == '_' else ''
        name, src, closure_params = rewrite_generator(func, input_names, all_names, suffix)
        gen_sources.append((name, src))
        # Extract closure values
        vals = []
        freevars = func.__code__.co_freevars if func.__closure__ else ()
        for pname in closure_params:
            if pname in freevars and func.__closure__:
                vals.append(func.__closure__[freevars.index(pname)].cell_contents)
            elif hasattr(func, '__globals__') and pname in func.__globals__:
                vals.append(func.__globals__[pname])
        closure_info.append((closure_params, vals))

    pyx_src = emit_generators_pyx(flat_ir, gen_sources, model_c_src)

    content = (model_c_src + pyx_src).encode()
    content_hash = hashlib.sha256(content).hexdigest()[:16]

    cache_dir = os.path.join(_default_model_cache_dir(), 'tb_gen')
    os.makedirs(cache_dir, exist_ok=True)

    ext_suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.so'
    so_path = os.path.join(cache_dir, f'tb_gen_{content_hash}{ext_suffix}')

    if not os.path.exists(so_path):
        build_dir = tempfile.mkdtemp(prefix='veripy_tbgen_')
        try:
            c_path = os.path.join(build_dir, 'model_src.c')
            h_path = os.path.join(build_dir, 'model.h')
            pyx_path = os.path.join(build_dir, 'tb_gen.pyx')

            with open(c_path, 'w') as f:
                f.write(model_c_src)
            with open(h_path, 'w') as f:
                f.write(header_src)
            with open(pyx_path, 'w') as f:
                f.write(pyx_src)

            from Cython.Compiler.Main import compile as cy_compile
            from Cython.Compiler.Options import CompilationOptions
            opts = CompilationOptions(
                language_level=3,
                include_path=[build_dir],
            )
            result = cy_compile(pyx_path, opts)
            if result.num_errors > 0:
                raise RuntimeError(
                    f'Generator Cython compilation failed with {result.num_errors} errors\n'
                    f'Source:\n{pyx_src}')

            cy_c_path = pyx_path.replace('.pyx', '.c')
            py_inc = sysconfig.get_path('include')
            cc = os.environ.get('CC', 'cc')
            flag = '-dynamiclib' if os.uname().sysname == 'Darwin' else '-shared'
            cmd = [cc, '-O3', '-march=native', '-fPIC', flag,
                   '-I', build_dir, '-I', py_inc,
                   '-o', so_path, cy_c_path, c_path,
                   '-undefined', 'dynamic_lookup']

            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(
                    f'Generator compilation failed:\n{r.stderr}\n'
                    f'Source:\n{pyx_src}')
        finally:
            import shutil
            shutil.rmtree(build_dir, ignore_errors=True)

    spec = importlib.util.spec_from_file_location('tb_gen', so_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    return {name: (getattr(mod, name), ci) for (name, _), ci in zip(gen_sources, closure_info)}
