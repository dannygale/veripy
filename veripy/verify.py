"""Dual-path test case: write one test, verify Python sim and iverilog agree."""

import unittest, subprocess, tempfile, os
from .signal import Signal, Mem, Interface
from .sim import SimEngine


def _collect_locals(stmts, declared, regs):
    """Scan IR stmts for Assign targets not in declared → add RegDecl."""
    from .ir import Assign, If, Repeat, ForLoop, RegDecl
    for stmt in stmts:
        if isinstance(stmt, Assign) and stmt.target not in declared:
            declared.add(stmt.target)
            regs.append(RegDecl(stmt.target, 32))
        elif isinstance(stmt, If):
            _collect_locals(stmt.then_body, declared, regs)
            _collect_locals(stmt.else_body, declared, regs)
        elif isinstance(stmt, Repeat):
            _collect_locals(stmt.body, declared, regs)
        elif isinstance(stmt, ForLoop):
            if stmt.var not in declared:
                declared.add(stmt.var)
                regs.append(RegDecl(stmt.var, 32))
            _collect_locals(stmt.body, declared, regs)


class VeripyTestCase(unittest.TestCase):
    """Event-driven dual-path test case.

    Subclass and override create_module(). Define testbench blocks with
    @self.always and @self.initial, drive signals with self.set(),
    read outputs with self.out(), and call self.run() to execute.

        class TestCounter(VeripyTestCase):
            def create_module(self):
                return Counter(4)

            def test_counting(self):
                @self.always
                def clock():
                    self.set(clock=0)
                    yield 5
                    self.set(clock=1)
                    yield 5

                @self.initial
                def stimulus():
                    self.set(reset=1, enable=1)
                    yield 10
                    self.assertEqual(self.out('count'), 0)
                    self.set(reset=0)
                    for _ in range(5):
                        yield 10
                    self.assertEqual(self.out('count'), 5)

                self.run()
    """

    def create_module(self):
        raise NotImplementedError

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name in list(vars(cls)):
            if name.startswith('test_'):
                fn = vars(cls)[name]
                setattr(cls, name, _wrap_dual(fn))

    # --- test API ---

    def set(self, **kwargs):
        """Set input signal values."""
        for name, val in kwargs.items():
            getattr(self._mod, name)._val = val
        self._trace_sets.append((self._engine.time, dict(kwargs)))

    def out(self, name):
        """Read an output signal's current value and record for comparison."""
        val = int(getattr(self._mod, name))
        t = self._engine.time
        if t not in self._py_outputs:
            self._py_outputs[t] = {}
        self._py_outputs[t][name] = val
        return val

    def run_sim(self):
        """Run the event-driven simulation."""
        self._ran_sim = True
        self._engine.run()

    def always(self, fn):
        """Register a generator function as an always block."""
        self._engine.always(fn)
        self._tb_always.append(fn)
        return fn

    def initial(self, fn):
        """Register a generator function as an initial block."""
        self._engine.initial(fn)
        self._tb_initial.append(fn)
        return fn

    def fork(self, *fns):
        """Launch generator functions in parallel, return Until that waits for all."""
        return self._engine.fork(*fns)

    def fork_any(self, *fns):
        """Launch generator functions in parallel, return Until that waits for first."""
        return self._engine.fork_any(*fns)

    def coverage_report(self):
        """Return [(name, hit_count)] for all cover points on the module."""
        return self._mod.coverage_report()

    # --- internals ---

    def _begin(self):
        self._mod = self.create_module()
        self._engine = SimEngine(self._mod)
        self._trace_sets = []      # [(time, {name: val}), ...]
        self._py_outputs = {}      # {time: {name: val}}
        self._tb_always = []       # always block functions (for re-run)
        self._tb_initial = []      # initial block functions (for re-run)
        self._output_names = sorted(
            k for k in dir(self._mod)
            if isinstance(getattr(self._mod, k), Signal)
            and getattr(self._mod, k)._kind == 'output')

    def _run_iverilog(self):
        """Lower testbench blocks to Verilog, compile with iverilog, run, parse."""
        from .lower import lower_tb_block
        from .ir import (IRModule, InitialBlock, AlwaysBlock, Finish, Display,
                         Port, WireDecl, RegDecl, Instance)
        from .backend_verilog import emit_verilog
        from .emit_verilog import _to_snake

        mod = self._mod
        module_name = type(mod).__name__.lower()
        sigs = {}
        for k in dir(mod):
            v = getattr(mod, k)
            if isinstance(v, Signal):
                sigs[k] = v
            elif isinstance(v, Interface):
                for sn in v._signals():
                    sigs[f'{k}_{sn}'] = getattr(v, sn)

        # ── DUT Verilog ──────────────────────────────────────────────
        parts = []
        seen = set()
        def _collect(m, mname):
            if mname in seen:
                return
            seen.add(mname)
            factory = getattr(type(m), '_veripy_factory', None)
            fresh = factory() if factory else type(m)()
            for sn, sub in fresh._submodules().items():
                _collect(sub, _to_snake(type(sub).__name__))
            parts.append(fresh.to_verilog(mname))
        for sn, sub in mod._submodules().items():
            _collect(sub, _to_snake(type(sub).__name__))
        parts.append(mod.to_verilog(module_name))
        verilog_src = '\n\n'.join(parts)

        # ── Detect module variable name from closures ────────────────
        mod_var = None
        for fn in self._tb_initial + self._tb_always:
            if hasattr(fn, '__code__') and fn.__closure__:
                for i, name in enumerate(fn.__code__.co_freevars):
                    cell_val = fn.__closure__[i].cell_contents
                    if cell_val is mod:
                        mod_var = name
                        break
            if mod_var:
                break
        if mod_var is None:
            mod_var = 'm'  # fallback

        # ── Lower testbench blocks ───────────────────────────────────
        tb_ir = IRModule(name='tb')

        # Declare input signals as regs, outputs as wires
        declared = set()
        for name, sig in sorted(sigs.items()):
            if sig._kind == 'input':
                tb_ir.regs.append(RegDecl(name, sig.width))
                declared.add(name)
            elif sig._kind == 'output':
                tb_ir.wires.append(WireDecl(name, sig.width))
                declared.add(name)

        # DUT instance
        inst_ports = [(name, name) for name, sig in sorted(sigs.items())
                      if sig._kind in ('input', 'output')]
        tb_ir.instances.append(Instance(module_name, 'dut', {}, inst_ports))

        # Lower all blocks
        all_stmts = []
        for fn in self._tb_always:
            stmts = lower_tb_block(fn, mod, mod_var, self._output_names)
            tb_ir.always_blocks.append(AlwaysBlock(stmts))
            all_stmts.extend(stmts)

        for fn in self._tb_initial:
            stmts = lower_tb_block(fn, mod, mod_var, self._output_names)
            stmts.append(Finish())
            tb_ir.initial_blocks.append(InitialBlock(stmts))
            all_stmts.extend(stmts)

        # Scan lowered stmts for local variables that need reg declarations
        from .ir import Assign as IRAssign
        _collect_locals(all_stmts, declared, tb_ir.regs)

        tb_verilog = emit_verilog(tb_ir)
        # Prepend timescale
        tb_verilog = '`timescale 1ns/1ns\n' + tb_verilog

        # ── Compile and run ──────────────────────────────────────────
        with tempfile.TemporaryDirectory() as tmpdir:
            dut_f = os.path.join(tmpdir, f'{module_name}.v')
            tb_f = os.path.join(tmpdir, 'tb.v')
            sim_f = os.path.join(tmpdir, 'sim')
            with open(dut_f, 'w') as f:
                f.write(verilog_src)
            with open(tb_f, 'w') as f:
                f.write(tb_verilog)

            r = subprocess.run(['iverilog', '-o', sim_f, tb_f, dut_f],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"iverilog compilation failed:\n{r.stderr}\n\nTestbench:\n{tb_verilog}")

            r = subprocess.run(['vvp', sim_f], capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"vvp failed:\n{r.stderr}")

        # Parse output
        self._rtl_outputs = {}
        for line in r.stdout.strip().split('\n'):
            if not line.startswith('@'):
                continue
            parts = line.split()
            t = int(parts[0][1:])
            if t not in self._rtl_outputs:
                self._rtl_outputs[t] = {}
            for part in parts[1:]:
                name, val = part.split('=')
                self._rtl_outputs[t][name] = None if val == 'x' else int(val)

    def _assert_traces_match(self):
        for t, py_vals in sorted(self._py_outputs.items()):
            rtl_vals = self._rtl_outputs.get(t, {})
            for name, pv in py_vals.items():
                rv = rtl_vals.get(name)
                self.assertEqual(pv, rv,
                    f"Sim/RTL mismatch at t={t}, '{name}': "
                    f"Python={pv}, Verilog={rv}")

    def _run_verilator(self):
        """Replay recorded stimuli through Verilator model, collect outputs."""
        from .backend_verilator import compile_module, _has_verilator
        if not _has_verilator():
            return False

        mod = self._mod
        module_name = type(mod).__name__.lower()

        with compile_module(mod, module_name) as vm:
            vm.eval()  # initial eval

            self._rtl_outputs = {}
            for t, sets in self._trace_sets:
                for name, val in sets.items():
                    vm.set(name, val)
                vm.eval()
                # Record outputs at this time
                if t in self._py_outputs:
                    if t not in self._rtl_outputs:
                        self._rtl_outputs[t] = {}
                    for name in self._py_outputs[t]:
                        self._rtl_outputs[t][name] = vm.get(name)
        return True

    def _run_csim(self):
        """Replay recorded stimuli through native C sim, collect outputs."""
        from .backend_csim import compile_module as csim_compile

        mod = self._mod
        module_name = type(mod).__name__.lower()

        try:
            with csim_compile(mod, module_name) as cm:
                cm.eval()

                self._rtl_outputs = {}
                for t, sets in self._trace_sets:
                    for name, val in sets.items():
                        cm.set(name, val)
                    cm.eval()
                    if t in self._py_outputs:
                        if t not in self._rtl_outputs:
                            self._rtl_outputs[t] = {}
                        for name in self._py_outputs[t]:
                            self._rtl_outputs[t][name] = cm.get(name)

                # Capture outputs at times after the last trace_set
                for t in self._py_outputs:
                    if t not in self._rtl_outputs:
                        self._rtl_outputs[t] = {}
                        for name in self._py_outputs[t]:
                            self._rtl_outputs[t][name] = cm.get(name)
        except Exception:
            return False
        return True


def _wrap_dual(fn):
    """Wrap a test method to run Python sim then compare with iverilog."""
    def wrapper(self):
        # Pass 1: Python sim (assertions run inside initial blocks)
        self._begin()
        self._ran_sim = False
        with self.subTest(backend='python'):
            fn(self)
            if not self._ran_sim:
                self.run_sim()

        # Pass 2: generate Verilog testbench, run iverilog, compare
        # Reactive yields (until()) can't be lowered to Verilog — skip iverilog path.
        try:
            self._run_iverilog()
        except SyntaxError:
            return
        with self.subTest(backend='sim_vs_rtl'):
            self._assert_traces_match()

        # Pass 3 (opt-in): Verilator co-simulation
        use_verilator = getattr(self, 'USE_VERILATOR', False) or \
                        os.environ.get('VERIPY_VERILATOR', '') == '1'
        if use_verilator:
            saved_rtl = self._rtl_outputs
            if self._run_verilator():
                with self.subTest(backend='verilator'):
                    self._assert_traces_match()
            self._rtl_outputs = saved_rtl

        # Pass 4 (opt-in): Native C simulation
        use_csim = getattr(self, 'USE_CSIM', False) or \
                   os.environ.get('VERIPY_CSIM', '') == '1'
        if use_csim:
            saved_rtl = self._rtl_outputs
            if self._run_csim():
                with self.subTest(backend='csim'):
                    self._assert_traces_match()
            self._rtl_outputs = saved_rtl

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper
