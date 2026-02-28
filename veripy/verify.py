"""Dual-path test case: write one test, verify Python sim and iverilog agree."""

import unittest, subprocess, tempfile, os
from .signal import Signal, Mem
from .sim import SimEngine


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
        """Generate testbench from recorded trace, compile, run, parse."""
        mod = self._mod
        module_name = type(mod).__name__.lower()
        sigs = {k: getattr(mod, k) for k in dir(mod)
                if isinstance(getattr(mod, k), Signal)}

        # Verilog source
        from .lower import lower_module
        from .backend_verilog import emit_verilog
        from .emit_verilog import _to_snake
        if mod._submodules():
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
                parts.append(emit_verilog(lower_module(fresh, mname)))
            for sn, sub in mod._submodules().items():
                _collect(sub, _to_snake(type(sub).__name__))
            parts.append(emit_verilog(lower_module(mod, module_name)))
            verilog_src = '\n\n'.join(parts)
        else:
            verilog_src = emit_verilog(lower_module(mod, module_name))

        # Build testbench from trace
        tb = ['`timescale 1ns/1ps', 'module tb;']

        # Declare all input signals as regs
        for name, sig in sorted(sigs.items()):
            if sig._kind == 'input':
                w = f'[{sig.width-1}:0] ' if sig.width > 1 else ''
                tb.append(f'    reg {w}{name};')

        # Declare all output signals as wires
        for name in self._output_names:
            sig = sigs[name]
            w = f'[{sig.width-1}:0] ' if sig.width > 1 else ''
            tb.append(f'    wire {w}{name};')

        # Instantiate DUT
        ports = []
        for name, sig in sorted(sigs.items()):
            if sig._kind in ('input', 'output'):
                ports.append(f'.{name}({name})')
        tb.append(f'    {module_name} dut({", ".join(ports)});')

        # Generate initial block from trace
        tb.append('    initial begin')

        # Group sets by time, interleave with delays and output reads
        check_times = sorted(self._py_outputs.keys())
        all_times = sorted(set(t for t, _ in self._trace_sets) | set(check_times))

        prev_time = 0
        for t in all_times:
            if t > prev_time:
                tb.append(f'        #{t - prev_time};')
                prev_time = t

            # Apply signal changes at this time
            for st, changes in self._trace_sets:
                if st == t:
                    for name in sorted(changes):
                        tb.append(f'        {name} = {changes[name]};')

            # Emit $display for output reads at this time
            if t in self._py_outputs:
                fmt = ' '.join(f'{n}=%0d' for n in sorted(self._py_outputs[t]))
                args = ', '.join(sorted(self._py_outputs[t]))
                tb.append(f'        $display("@{t} {fmt}", {args});')

        tb.append('        $finish;')
        tb.append('    end')
        tb.append('endmodule')

        # Compile and run
        with tempfile.TemporaryDirectory() as tmpdir:
            dut_f = os.path.join(tmpdir, f'{module_name}.v')
            tb_f = os.path.join(tmpdir, 'tb.v')
            sim_f = os.path.join(tmpdir, 'sim')
            with open(dut_f, 'w') as f:
                f.write(verilog_src)
            with open(tb_f, 'w') as f:
                f.write('\n'.join(tb))

            r = subprocess.run(['iverilog', '-o', sim_f, tb_f, dut_f],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"iverilog compilation failed:\n{r.stderr}\n\nTestbench:\n" +
                          '\n'.join(tb))

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
        self._run_iverilog()
        with self.subTest(backend='sim_vs_rtl'):
            self._assert_traces_match()

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper
