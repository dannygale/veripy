"""TestBench: CySim-backed test case for VeriPy modules."""

import atexit, json, threading, unittest, subprocess, tempfile, os, random

# Seed Python's random from VERIPY_SEED if set (injected by parallel runner)
_env_seed = os.environ.get('VERIPY_SEED')
if _env_seed is not None:
    random.seed(int(_env_seed))

from .signal import Signal, Mem, Interface

# ── Free-standing decorators ─────────────────────────────────────────
_current_tb = threading.local()


def initial(fn):
    """Register a generator as an initial block on the active TestBench."""
    return _current_tb.tb.initial(fn)


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


class _SignalNamespace:
    """Signal namespace: ``dut.reset = 0`` → set, ``dut.count`` → get."""
    __slots__ = ('_tb',)

    def __init__(self, tb):
        object.__setattr__(self, '_tb', tb)

    def __getattr__(self, name):
        return self._tb.get(name)

    def __setattr__(self, name, value):
        self._tb.set(**{name: int(value)})


class _CySignalProxy:
    """Proxy that routes signal set/read through a CySimModel."""
    __slots__ = ('_name', '_cm', '_kind', 'width', '_mask')

    def __init__(self, name, cmodel, sig):
        self._name = name
        self._cm = cmodel
        self._kind = sig._kind
        self.width = sig.width
        self._mask = sig._mask

    @property
    def name(self):
        return self._name

    def set(self, value):
        self._cm.set(self._name, int(value))

    def __int__(self):
        return self._cm.get(self._name)

    def __bool__(self):
        return self._cm.get(self._name) != 0

    @property
    def _val(self):
        return self._cm.get(self._name)

    @_val.setter
    def _val(self, v):
        if self._kind == 'input':
            self._cm.set(self._name, int(v) & self._mask)


def _patch_signals_for_cysim(mod, cmodel):
    """Replace module signals with proxies that route through CySimModel."""
    for name in list(dir(mod)):
        sig = getattr(mod, name, None)
        if isinstance(sig, Signal):
            object.__setattr__(mod, name, _CySignalProxy(name, cmodel, sig))


class TestBench(unittest.TestCase):
    """CySim-backed test bench for VeriPy modules.

    Subclass and override create_module(). Write test_* methods using
    @initial / @always decorators and self.dut for signal access.

        class TestCounter(TestBench):
            def create_module(self):
                return Counter(4)

            def test_counting(self):
                dut = self.dut
                self.clock('clock', 10)

                @initial
                def stim():
                    dut.reset = 1; dut.enable = 1
                    yield 10
                    dut.reset = 0
                    for _ in range(5):
                        yield 10
                    assert dut.count == 5
    """

    def create_module(self):
        raise NotImplementedError

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name in list(vars(cls)):
            if name.startswith('test_'):
                fn = vars(cls)[name]
                setattr(cls, name, _wrap_testbench(fn))

    # --- test API ---

    @property
    def module(self):
        """The module under test."""
        return self._mod

    @property
    def dut(self):
        """Signal namespace: ``dut.reset = 0`` sets, ``dut.count`` reads."""
        return _SignalNamespace(self)

    def set(self, **kwargs):
        """Set input signal values."""
        cm = self._cysim_model
        if cm is not None:
            for name, val in kwargs.items():
                cm.set(name, val)
        else:
            for name, val in kwargs.items():
                getattr(self._mod, name)._val = val

    def get(self, name):
        """Read a signal value."""
        cm = self._cysim_model
        if cm is not None:
            return cm.get(name)
        return int(getattr(self._mod, name))

    def out(self, name):
        """Read an output signal's current value."""
        return self.get(name)

    def run_sim(self):
        """Run the event-driven simulation."""
        self._ran_sim = True
        self._engine.run()

    def clock(self, name, period=10):
        """Register a clock driver."""
        self._clock_name = name
        self._clock_period = period
        self._engine.clock(name, period)

    def run_cycles(self, n):
        """Run *n* clock cycles entirely in C."""
        self._engine.run_cycles(n)

    def peripheral(self, fn):
        """Register a combinational callback fired every time unit."""
        self._peripherals.append(fn)
        @self.always
        def _periph():
            fn()
            yield 1
        return fn

    def run_testbench(self, clock, period=10):
        """Decorator: register clock and run sim after decorated fn."""
        self.clock(clock, period)
        def decorator(fn):
            self.initial(fn)
            self.run_sim()
        return decorator

    def run(self, result=None):
        if hasattr(self, '_mod'):
            raise RuntimeError(
                "Did you mean self.run_sim()? "
                "self.run() invokes the unittest runner, not the simulation.")
        return super().run(result)

    def reset(self):
        """Reset module and simulation state."""
        self._begin()

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
        from .backend_csim import compile_cysim
        mod = self.create_module()
        module_name = type(mod).__name__.lower()
        self._ctx = compile_cysim(mod, module_name)
        cm = self._ctx.__enter__()
        self._mod = self.create_module()
        self._engine = self._ctx.engine()
        self._cysim_model = cm
        self._tb_always = []
        self._tb_initial = []
        self._clock_name = None
        self._clock_period = None
        self._peripherals = []
        self._ran_sim = False
        self._output_names = sorted(
            k for k in dir(self._mod)
            if isinstance(getattr(self._mod, k), Signal)
            and getattr(self._mod, k)._kind == 'output')
        _patch_signals_for_cysim(self._mod, cm)

    def _end(self):
        ctx = getattr(self, '_ctx', None)
        if ctx is not None:
            ctx.__exit__(None, None, None)
            self._ctx = None
        self._cysim_model = None

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
            if isinstance(v, (Signal, _CySignalProxy)):
                sigs[k] = v
            elif isinstance(v, Interface):
                for sn in v._signals():
                    sigs[f'{k}_{sn}'] = getattr(v, sn)

        # ── DUT Verilog (cached per class) ───────────────────────────
        cache_key = type(mod)
        if not hasattr(self.__class__, '_dut_verilog_cache'):
            self.__class__._dut_verilog_cache = {}
        if cache_key in self.__class__._dut_verilog_cache:
            verilog_src = self.__class__._dut_verilog_cache[cache_key]
        else:
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
            orig_mod = self.create_module()
            for sn, sub in orig_mod._submodules().items():
                _collect(sub, _to_snake(type(sub).__name__))
            parts.append(orig_mod.to_verilog(module_name))
            verilog_src = '\n\n'.join(parts)
            self.__class__._dut_verilog_cache[cache_key] = verilog_src

        # ── Detect module variable name from closures ────────────────
        orig_mod = self.create_module()
        mod_var = None
        for fn in self._tb_initial + self._tb_always:
            if hasattr(fn, '__code__') and fn.__closure__:
                for i, name in enumerate(fn.__code__.co_freevars):
                    try:
                        cell_val = fn.__closure__[i].cell_contents
                        if cell_val is orig_mod or type(cell_val) is type(orig_mod):
                            mod_var = name
                            break
                    except ValueError:
                        pass
            if mod_var:
                break
        if mod_var is None:
            mod_var = 'm'

        # ── Lower testbench blocks ───────────────────────────────────
        tb_ir = IRModule(name='tb')

        declared = set()
        for name, sig in sorted(sigs.items()):
            kind = sig._kind if hasattr(sig, '_kind') else getattr(sig, '_kind', None)
            if kind == 'input':
                tb_ir.regs.append(RegDecl(name, sig.width))
                declared.add(name)
            elif kind == 'output':
                tb_ir.wires.append(WireDecl(name, sig.width))
                declared.add(name)

        inst_ports = [(name, name) for name, sig in sorted(sigs.items())
                      if getattr(sig, '_kind', None) in ('input', 'output')]
        tb_ir.instances.append(Instance(module_name, 'dut', {}, inst_ports))

        all_stmts = []
        for fn in self._tb_always:
            stmts = lower_tb_block(fn, orig_mod, mod_var, self._output_names)
            tb_ir.always_blocks.append(AlwaysBlock(stmts))
            all_stmts.extend(stmts)

        for fn in self._tb_initial:
            stmts = lower_tb_block(fn, orig_mod, mod_var, self._output_names)
            stmts.append(Finish())
            tb_ir.initial_blocks.append(InitialBlock(stmts))
            all_stmts.extend(stmts)

        from .ir import Assign as IRAssign
        _collect_locals(all_stmts, declared, tb_ir.regs)

        tb_verilog = '`timescale 1ns/1ns\n' + emit_verilog(tb_ir)

        with tempfile.TemporaryDirectory() as tmpdir:
            dut_f = os.path.join(tmpdir, f'{module_name}.v')
            tb_f  = os.path.join(tmpdir, 'tb.v')
            sim_f = os.path.join(tmpdir, 'sim')
            with open(dut_f, 'w') as f: f.write(verilog_src)
            with open(tb_f,  'w') as f: f.write(tb_verilog)

            r = subprocess.run(['iverilog', '-o', sim_f, tb_f, dut_f],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"iverilog compilation failed:\n{r.stderr}\n\nTestbench:\n{tb_verilog}")

            r = subprocess.run(['vvp', sim_f], capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"vvp failed:\n{r.stderr}")

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

    def _run_verilator(self):
        """Run test against Verilator-compiled model."""
        from .backend_verilator import compile_module, _has_verilator
        if not _has_verilator():
            return False
        mod = self.create_module()
        module_name = type(mod).__name__.lower()
        # Verilator path: re-run test with verilator model (future work)
        return False


def _wrap_testbench(fn):
    """Wrap a test method to compile via cysim and run."""
    def wrapper(self):
        _current_tb.tb = self
        self._begin()
        try:
            fn(self)
            if not self._ran_sim:
                self.run_sim()
            for name, hits in self._mod.coverage_report():
                _coverage_db[name] = _coverage_db.get(name, 0) + hits
        finally:
            self._end()

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper
