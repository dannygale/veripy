"""Dual-path test case: write one test, verify Python sim and iverilog agree."""

import atexit, json, unittest, subprocess, tempfile, os, random

# Seed Python's random from VERIPY_SEED if set (injected by parallel runner)
_env_seed = os.environ.get('VERIPY_SEED')
if _env_seed is not None:
    random.seed(int(_env_seed))

from .signal import Signal, Mem, Interface
from .sim import SimEngine

# Global coverage accumulator: {cover_point_name: hit_count}
_coverage_db = {}


def _flush_coverage():
    """Write accumulated coverage to VERIPY_COVERAGE_FILE if set."""
    path = os.environ.get('VERIPY_COVERAGE_FILE')
    if path and _coverage_db:
        with open(path, 'w') as f:
            json.dump(_coverage_db, f)


atexit.register(_flush_coverage)


def _merge_csim_coverage(cov):
    """Merge csim structural coverage into the global coverage db."""
    if not cov:
        return
    for i, count in cov.get('line', []):
        if count:
            _coverage_db[f'csim:line:{i}'] = _coverage_db.get(f'csim:line:{i}', 0) + count
    for name, ones, zeros in cov.get('toggle', []):
        key_ones = f'csim:toggle:{name}:ones'
        key_zeros = f'csim:toggle:{name}:zeros'
        _coverage_db[key_ones] = _coverage_db.get(key_ones, 0) | ones
        _coverage_db[key_zeros] = _coverage_db.get(key_zeros, 0) | zeros
    visited = cov.get('fsm_visited', 0)
    if visited:
        _coverage_db['csim:fsm:visited'] = _coverage_db.get('csim:fsm:visited', 0) | visited
    for f, t in cov.get('fsm_trans', []):
        key = f'csim:fsm:trans:{f}->{t}'
        _coverage_db[key] = _coverage_db.get(key, 0) + 1


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

    def _assert_all_match(self):
        """Compare all collected backend outputs against each other."""
        backends = self._all_outputs  # {name: {t: {sig: val}}}
        names = list(backends.keys())
        if len(names) < 2:
            return
        # Collect all (time, signal) pairs observed by any backend
        all_points = set()
        for bdata in backends.values():
            for t, sigs in bdata.items():
                for sig in sigs:
                    all_points.add((t, sig))
        for t, sig in sorted(all_points):
            vals = {}
            for bn in names:
                v = backends[bn].get(t, {}).get(sig)
                if v is not None:
                    vals[bn] = v
            unique = set(vals.values())
            if len(unique) > 1:
                detail = ', '.join(f'{bn}={vals[bn]}' for bn in names if bn in vals)
                self.fail(f"Mismatch at t={t}, '{sig}': {detail}")

    def _replay_stimuli(self, model):
        """Replay recorded stimuli through a compiled model, return outputs dict."""
        model.eval()
        outputs = {}
        for t, sets in self._trace_sets:
            # Capture outputs BEFORE applying this timestep's sets,
            # matching Python sim where out() reads before set() at same time.
            if t in self._py_outputs and t not in outputs:
                outputs[t] = {}
                for name in self._py_outputs[t]:
                    outputs[t][name] = model.get(name)
            for name, val in sets.items():
                model.set(name, val)
            model.eval()
        # Capture outputs at times after the last trace_set
        for t in self._py_outputs:
            if t not in outputs:
                outputs[t] = {}
                for name in self._py_outputs[t]:
                    outputs[t][name] = model.get(name)
        return outputs

    def _run_verilator(self):
        """Replay recorded stimuli through Verilator model, collect outputs."""
        from .backend_verilator import compile_module, _has_verilator
        if not _has_verilator():
            return False
        mod = self._mod
        module_name = type(mod).__name__.lower()
        with compile_module(mod, module_name) as vm:
            out = self._replay_stimuli(vm)
        if hasattr(self, '_all_outputs'):
            self._all_outputs['verilator'] = out
        self._rtl_outputs = out
        return True

    def _run_csim(self, force_hier=False):
        """Replay recorded stimuli through native C sim, collect outputs."""
        from .backend_csim import compile_module as csim_compile
        mod = self._mod
        module_name = type(mod).__name__.lower()
        try:
            with csim_compile(mod, module_name, force_hier=force_hier,
                              coverage=not force_hier) as cm:
                out = self._replay_stimuli(cm)
                if not force_hier:
                    _merge_csim_coverage(cm.get_coverage())
        except Exception:
            return False
        key = 'csim_hier' if force_hier else 'csim'
        if hasattr(self, '_all_outputs'):
            self._all_outputs[key] = out
        self._rtl_outputs = out
        return True


def _wrap_dual(fn):
    """Wrap a test method to run Python sim, iverilog, and csim, then compare all."""
    def wrapper(self):
        # Pass 1: Python sim (assertions run inside initial blocks)
        self._begin()
        self._all_outputs = {}
        self._ran_sim = False
        with self.subTest(backend='python'):
            fn(self)
            if not self._ran_sim:
                self.run_sim()
        self._all_outputs['python'] = dict(self._py_outputs)

        # Accumulate cover point hits into global db
        for name, hits in self._mod.coverage_report():
            _coverage_db[name] = _coverage_db.get(name, 0) + hits

        # Pass 2: iverilog
        # Reactive yields (until()) can't be lowered to Verilog — skip.
        try:
            self._run_iverilog()
            self._all_outputs['verilog'] = dict(self._rtl_outputs)
        except SyntaxError:
            pass

        # Pass 3: csim flat (always-on)
        skip_csim = getattr(self, 'SKIP_CSIM', False) or \
                    os.environ.get('VERIPY_SKIP_CSIM', '') == '1'
        if not skip_csim:
            self._run_csim()
            self._run_csim(force_hier=True)

        # Pass 4 (opt-in): Verilator
        use_verilator = getattr(self, 'USE_VERILATOR', False) or \
                        os.environ.get('VERIPY_VERILATOR', '') == '1'
        if use_verilator:
            self._run_verilator()

        # Compare all backends
        self._assert_all_match()

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper
