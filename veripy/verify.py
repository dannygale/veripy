"""Dual-path test case: write one test, verify Python sim and iverilog agree."""

import unittest, subprocess, tempfile, os
from .signal import Signal, Mem


class VeripyTestCase(unittest.TestCase):
    """Test case that runs each test against both Python sim and iverilog.

    Subclass and override create_module(). Use set(), tick(), out() in tests.
    Each test_* method automatically runs twice (Python, then Verilog) and
    asserts all outputs match on every cycle.

        class TestCounter(VeripyTestCase):
            def create_module(self):
                return Counter(4)

            def test_counting(self):
                self.set(reset=1, enable=1)
                self.tick()
                self.assertEqual(self.out('count'), 0)
                self.set(reset=0)
                for _ in range(5):
                    self.tick()
                self.assertEqual(self.out('count'), 5)
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
        """Set input signal values (sticky until changed)."""
        if self._backend == 'python':
            self._input_state.update(kwargs)

    def tick(self):
        """Advance one clock cycle."""
        self._cycle += 1
        if self._backend == 'python':
            for name, val in self._input_state.items():
                getattr(self._mod, name)._val = val
            self._mod.tick()
            self._trace_inputs.append(dict(self._input_state))
            self._py_outputs.append(
                {n: int(getattr(self._mod, n)) for n in self._output_names})

    def out(self, name):
        """Read an output signal's current value."""
        if self._backend == 'python':
            return int(getattr(self._mod, name))
        return self._rtl_outputs[self._cycle][name]

    # --- internals ---

    def _begin(self, backend):
        self._backend = backend
        self._cycle = -1
        if backend == 'python':
            self._mod = self.create_module()
            self._input_state = {}
            self._trace_inputs = []
            self._py_outputs = []
            self._output_names = sorted(
                k for k in dir(self._mod)
                if isinstance(getattr(self._mod, k), Signal)
                and getattr(self._mod, k)._kind == 'output')
            clk_list = self._mod._posedge_blocks
            self._clock = clk_list[0][0] if clk_list else None

    def _run_iverilog(self):
        """Generate testbench from recorded trace, compile, run, parse."""
        mod = self._mod
        module_name = type(mod).__name__.lower()
        sigs = {k: getattr(mod, k) for k in dir(mod)
                if isinstance(getattr(mod, k), Signal)}
        clk_name = self._clock.name if self._clock is not None else None

        # Verilog source
        from .emit_verilog import VerilogEmitter
        emitter = VerilogEmitter(mod, module_name)
        verilog_src = emitter.emit_all() if mod._submodules() else emitter.emit()

        # Testbench
        driven = set(self._trace_inputs[0].keys()) if self._trace_inputs else set()
        extra = sorted(k for k, s in sigs.items()
                       if s._kind == 'input' and k not in driven and k != clk_name)

        tb = ['`timescale 1ns/1ps', 'module tb;']
        if clk_name:
            tb.append(f'    reg {clk_name};')
        for name in sorted(driven) + extra:
            s = sigs[name]
            w = f'[{s.width-1}:0] ' if s.width > 1 else ''
            tb.append(f'    reg {w}{name};')
        for name in self._output_names:
            s = sigs[name]
            w = f'[{s.width-1}:0] ' if s.width > 1 else ''
            tb.append(f'    wire {w}{name};')

        ports = []
        if clk_name:
            ports.append(f'.{clk_name}({clk_name})')
        for name in sorted(driven) + extra + self._output_names:
            ports.append(f'.{name}({name})')
        tb.append(f'    {module_name} dut({", ".join(ports)});')

        if clk_name:
            tb.append(f'    initial {clk_name} = 0;')
            tb.append(f'    always #5 {clk_name} = ~{clk_name};')

        tb.append('    initial begin')
        for cyc, inp in enumerate(self._trace_inputs):
            for name in sorted(inp):
                tb.append(f'        {name} = {inp[name]};')
            if clk_name:
                tb.append(f'        @(posedge {clk_name}); #1;')
            else:
                tb.append('        #10;')
            fmt = ' '.join(f'{n}=%0d' for n in self._output_names)
            args = ', '.join(self._output_names)
            tb.append(f'        $display("@{cyc} {fmt}", {args});')
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
                self.fail(f"iverilog compilation failed:\n{r.stderr}")

            r = subprocess.run(['vvp', sim_f], capture_output=True, text=True)
            if r.returncode != 0:
                self.fail(f"vvp failed:\n{r.stderr}")

        # Parse output
        n = len(self._trace_inputs)
        self._rtl_outputs = [{} for _ in range(n)]
        for line in r.stdout.strip().split('\n'):
            if not line.startswith('@'):
                continue
            parts = line.split()
            cyc = int(parts[0][1:])
            for part in parts[1:]:
                name, val = part.split('=')
                self._rtl_outputs[cyc][name] = None if val == 'x' else int(val)

    def _assert_traces_match(self):
        for cyc in range(len(self._py_outputs)):
            for name in self._output_names:
                pv = self._py_outputs[cyc][name]
                rv = self._rtl_outputs[cyc][name]
                self.assertEqual(pv, rv,
                    f"Sim/RTL mismatch cycle {cyc}, '{name}': "
                    f"Python={pv}, Verilog={rv}")


def _wrap_dual(fn):
    """Wrap a test method to run against both Python sim and iverilog."""
    def wrapper(self):
        # Pass 1: Python sim
        self._begin('python')
        with self.subTest(backend='python'):
            fn(self)

        # Run iverilog with recorded stimulus
        self._run_iverilog()

        # Assert all outputs match on every cycle
        with self.subTest(backend='sim_vs_rtl'):
            self._assert_traces_match()

        # Pass 2: same assertions against Verilog outputs
        self._begin('verilog')
        with self.subTest(backend='verilog'):
            fn(self)

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper
