"""Tests for equivalence checking backend."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
import tempfile

from veripy import Module, Input, Output, Register, module, posedge
from veripy.context import comb, always
from veripy.lower import lower_module
from veripy.backend_verilog import emit_verilog
from veripy.ir import (
    Const, Sig, BinOp, Assign, ContAssign, CombBlock, SeqBlock, If,
    Port, RegDecl, IRModule,
)
from veripy.backend_equiv import emit_equiv_script, _check_ports_match, _fmt_ports


def _counter_ir(name='counter'):
    """Simple 4-bit counter IR for testing."""
    return IRModule(
        name=name,
        ports=[Port('clock', 'input', 1), Port('reset', 'input', 1),
               Port('enable', 'input', 1), Port('count', 'output', 4)],
        regs=[RegDecl('cnt', 4)],
        assigns=[ContAssign('count', Sig('cnt'))],
        seq_blocks=[SeqBlock(
            edges=[('posedge', 'clock')],
            stmts=[If(Sig('reset'),
                       [Assign('cnt', Const(0), False)],
                       [If(Sig('enable'),
                           [Assign('cnt', BinOp('+', Sig('cnt'), Const(1)), False)],
                           [])])],
            locals={},
        )],
    )


# ── Script generation (hand-crafted IR) ─────────────────────────────

class TestEmitEquivScript(unittest.TestCase):
    def test_basic_script(self):
        gold = _counter_ir('gold_counter')
        gate = _counter_ir('gate_counter')
        script = emit_equiv_script(gold, gate)
        self.assertIn('read_verilog gold.v', script)
        self.assertIn('rename gold_counter gold', script)
        self.assertIn('read_verilog gate.v', script)
        self.assertIn('rename gate_counter gate', script)
        self.assertIn('equiv_make gold gate equiv', script)
        self.assertIn('equiv_simple', script)
        self.assertIn('equiv_induct', script)
        self.assertIn('equiv_status -assert', script)

    def test_same_module_name(self):
        gold = _counter_ir('counter')
        gate = _counter_ir('counter')
        script = emit_equiv_script(gold, gate)
        self.assertIn('rename counter gold', script)
        self.assertIn('rename counter gate', script)

    def test_script_ends_with_newline(self):
        script = emit_equiv_script(_counter_ir('a'), _counter_ir('b'))
        self.assertTrue(script.endswith('\n'))

    def test_prep_top_equiv(self):
        script = emit_equiv_script(_counter_ir('a'), _counter_ir('b'))
        self.assertIn('prep -top equiv', script)


# ── Port checking ────────────────────────────────────────────────────

class TestPortCheck(unittest.TestCase):
    def test_matching_ports_ok(self):
        gold = _counter_ir()
        gate = _counter_ir('other')
        _check_ports_match(gold, gate)

    def test_missing_port_raises(self):
        gold = _counter_ir()
        gate = IRModule(
            name='gate',
            ports=[Port('clock', 'input', 1), Port('reset', 'input', 1),
                   Port('count', 'output', 4)],  # missing 'enable'
        )
        with self.assertRaises(ValueError) as ctx:
            _check_ports_match(gold, gate)
        self.assertIn('enable', str(ctx.exception))

    def test_width_mismatch_raises(self):
        gold = _counter_ir()
        gate = IRModule(
            name='gate',
            ports=[Port('clock', 'input', 1), Port('reset', 'input', 1),
                   Port('enable', 'input', 1), Port('count', 'output', 8)],
        )
        with self.assertRaises(ValueError):
            _check_ports_match(gold, gate)

    def test_direction_mismatch_raises(self):
        gold = _counter_ir()
        gate = IRModule(
            name='gate',
            ports=[Port('clock', 'input', 1), Port('reset', 'input', 1),
                   Port('enable', 'output', 1), Port('count', 'output', 4)],
        )
        with self.assertRaises(ValueError):
            _check_ports_match(gold, gate)

    def test_extra_port_in_gate(self):
        gold = IRModule(name='g', ports=[Port('a', 'input', 1)])
        gate = IRModule(name='g', ports=[Port('a', 'input', 1),
                                         Port('b', 'output', 1)])
        with self.assertRaises(ValueError) as ctx:
            _check_ports_match(gold, gate)
        self.assertIn('extra in gate', str(ctx.exception))

    def test_empty_ports_match(self):
        gold = IRModule(name='a', ports=[])
        gate = IRModule(name='b', ports=[])
        _check_ports_match(gold, gate)


# ── _fmt_ports helper ────────────────────────────────────────────────

class TestFmtPorts(unittest.TestCase):
    def test_single_port(self):
        result = _fmt_ports({('clk', 'input', 1)})
        self.assertEqual(result, 'input clk[1]')

    def test_multiple_sorted(self):
        ports = {('b', 'output', 8), ('a', 'input', 1)}
        result = _fmt_ports(ports)
        self.assertIn('input a[1]', result)
        self.assertIn('output b[8]', result)
        # 'a' should come before 'b' (sorted)
        self.assertTrue(result.index('a[1]') < result.index('b[8]'))


# ── End-to-end: real VeriPy modules → lower → equiv ─────────────────

class Counter(Module):
    def __init__(self, n=4):
        self.clock = Input()
        self.reset = Input()
        self.enable = Input()
        self.count = Output(n)
        self.cnt = Register(n)
        super().__init__()

        @self.comb
        def drive():
            self.count = self.cnt

        @self.posedge(self.clock)
        def inc():
            if self.reset:
                self.cnt = 0
            elif self.enable:
                self.cnt = self.cnt + 1


class TestEndToEndClassBased(unittest.TestCase):
    def test_equiv_script_from_modules(self):
        gold_ir = lower_module(Counter(), 'gold_counter')
        gate_ir = lower_module(Counter(), 'gate_counter')
        script = emit_equiv_script(gold_ir, gate_ir)
        self.assertIn('rename gold_counter gold', script)
        self.assertIn('rename gate_counter gate', script)
        self.assertIn('equiv_make', script)

    def test_port_mismatch_different_widths(self):
        gold_ir = lower_module(Counter(n=4), 'gold')
        gate_ir = lower_module(Counter(n=8), 'gate')
        with self.assertRaises(ValueError):
            emit_equiv_script(gold_ir, gate_ir)

    def test_verilog_emits_for_both(self):
        gold_ir = lower_module(Counter(), 'gold')
        gate_ir = lower_module(Counter(), 'gate')
        gold_v = emit_verilog(gold_ir)
        gate_v = emit_verilog(gate_ir)
        self.assertIn('module gold', gold_v)
        self.assertIn('module gate', gate_v)


class TestEndToEndModuleDecorator(unittest.TestCase):
    def test_equiv_script_from_decorator(self):
        @module
        def my_counter(n=4):
            clock = Input()
            reset = Input()
            enable = Input()
            count = Output(n)
            cnt = Register(n)

            @comb
            def drive():
                count = cnt

            @always(posedge(clock))
            def inc():
                if reset:
                    cnt = 0
                elif enable:
                    cnt = cnt + 1

        gold_ir = lower_module(my_counter(), 'gold')
        gate_ir = lower_module(my_counter(), 'gate')
        script = emit_equiv_script(gold_ir, gate_ir)
        self.assertIn('rename gold gold', script)
        self.assertIn('rename gate gate', script)
        self.assertIn('equiv_status -assert', script)


# ── CLI cmd_equiv integration ────────────────────────────────────────

class TestCmdEquiv(unittest.TestCase):
    _COUNTER_SRC = '''\
from veripy import Module, Input, Output, Register

class Counter(Module):
    def __init__(self):
        self.clock = Input()
        self.reset = Input()
        self.count = Output(4)
        self.cnt = Register(4)
        super().__init__()

        @self.comb
        def drive():
            self.count = self.cnt

        @self.posedge(self.clock)
        def inc():
            if self.reset:
                self.cnt = 0
            else:
                self.cnt = self.cnt + 1
'''

    def test_writes_output_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gold_py = os.path.join(tmpdir, 'gold.py')
            gate_py = os.path.join(tmpdir, 'gate.py')
            out_dir = os.path.join(tmpdir, 'out')
            for p in (gold_py, gate_py):
                with open(p, 'w') as f:
                    f.write(self._COUNTER_SRC)

            from veripy.cli import cmd_equiv
            import argparse
            args = argparse.Namespace(
                gold=gold_py, gate=gate_py, output=out_dir,
                gold_module=None, gate_module=None,
                gold_param=[], gate_param=[], run=False,
            )
            cmd_equiv(args)

            self.assertTrue(os.path.isfile(os.path.join(out_dir, 'gold.v')))
            self.assertTrue(os.path.isfile(os.path.join(out_dir, 'gate.v')))
            self.assertTrue(os.path.isfile(os.path.join(out_dir, 'equiv.ys')))

            with open(os.path.join(out_dir, 'equiv.ys')) as f:
                script = f.read()
            self.assertIn('equiv_make', script)

    def test_error_no_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_py = os.path.join(tmpdir, 'empty.py')
            with open(empty_py, 'w') as f:
                f.write('x = 1\n')

            from veripy.cli import cmd_equiv
            import argparse
            args = argparse.Namespace(
                gold=empty_py, gate=empty_py, output=tmpdir,
                gold_module=None, gate_module=None,
                gold_param=[], gate_param=[], run=False,
            )
            with self.assertRaises(SystemExit):
                cmd_equiv(args)


if __name__ == '__main__':
    unittest.main()
