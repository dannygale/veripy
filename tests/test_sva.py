"""Tests for SVA temporal sequence IR nodes, builder API, and string parser."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest

from veripy.ir import (
    Sig, Const, Compare, BoolOp, UnaryOp,
    SeqBool, SeqConcat, SeqRepeat, SeqAnd, SeqOr, SeqNot,
    SeqImplication, SeqWithin, SeqEventually,
    TemporalProperty, IRModule,
)
from veripy.sva import seq, parse_sva, Seq, _PendingDelay
from veripy.backend_verilog import emit_verilog
from veripy.backend_formal import emit_sby


# ── IR node construction ─────────────────────────────────────────────

class TestSeqIRNodes(unittest.TestCase):
    def test_seq_bool(self):
        n = SeqBool(Sig('valid'))
        self.assertIsInstance(n, SeqBool)
        self.assertEqual(n.expr, Sig('valid'))

    def test_seq_concat_defaults(self):
        n = SeqConcat(SeqBool(Sig('a')), SeqBool(Sig('b')))
        self.assertEqual(n.lo, 1)
        self.assertEqual(n.hi, 1)

    def test_seq_repeat_defaults(self):
        n = SeqRepeat(SeqBool(Sig('a')))
        self.assertEqual(n.lo, 1)
        self.assertEqual(n.hi, 1)

    def test_seq_implication_default_overlapping(self):
        n = SeqImplication(SeqBool(Sig('a')), SeqBool(Sig('b')))
        self.assertTrue(n.overlapping)

    def test_temporal_property_fields(self):
        tp = TemporalProperty('assert', 'clk', 'posedge',
                              SeqBool(Sig('valid')), 'my_prop')
        self.assertEqual(tp.kind, 'assert')
        self.assertEqual(tp.clock, 'clk')
        self.assertEqual(tp.name, 'my_prop')

    def test_irmodule_has_temporal_props(self):
        ir = IRModule('m')
        self.assertEqual(ir.temporal_props, [])


# ── Builder API ──────────────────────────────────────────────────────

class TestSeqBuilder(unittest.TestCase):
    def test_seq_from_sig(self):
        s = seq(Sig('valid'))
        self.assertIsInstance(s, Seq)
        self.assertIsInstance(s.ir, SeqBool)

    def test_seq_from_const(self):
        s = seq(Const(1))
        self.assertIsInstance(s.ir, SeqBool)
        self.assertEqual(s.ir.expr, Const(1))

    def test_then_default_delay(self):
        s = seq(Sig('a')).then(seq(Sig('b')))
        self.assertIsInstance(s.ir, SeqConcat)
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, 1)

    def test_then_custom_delay(self):
        s = seq(Sig('a')).then(seq(Sig('b')), lo=2, hi=4)
        self.assertEqual(s.ir.lo, 2)
        self.assertEqual(s.ir.hi, 4)

    def test_delay_then(self):
        pending = seq(Sig('a')).delay(3)
        self.assertIsInstance(pending, _PendingDelay)
        s = pending.then(seq(Sig('b')))
        self.assertIsInstance(s.ir, SeqConcat)
        self.assertEqual(s.ir.lo, 3)
        self.assertEqual(s.ir.hi, 3)

    def test_delay_range_then(self):
        s = seq(Sig('a')).delay(1, 3).then(seq(Sig('b')))
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, 3)

    def test_repeat_exact(self):
        s = seq(Sig('a')).repeat(4)
        self.assertIsInstance(s.ir, SeqRepeat)
        self.assertEqual(s.ir.lo, 4)
        self.assertEqual(s.ir.hi, 4)

    def test_repeat_range(self):
        s = seq(Sig('a')).repeat(1, 3)
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, 3)

    def test_repeat_star(self):
        s = seq(Sig('a')).repeat_star()
        self.assertEqual(s.ir.lo, 0)
        self.assertEqual(s.ir.hi, -1)

    def test_repeat_plus(self):
        s = seq(Sig('a')).repeat_plus()
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, -1)

    def test_implies_overlapping(self):
        s = seq(Sig('req')).implies(seq(Sig('ack')))
        self.assertIsInstance(s.ir, SeqImplication)
        self.assertTrue(s.ir.overlapping)

    def test_implies_non_overlapping(self):
        s = seq(Sig('req')).implies(seq(Sig('ack')), overlapping=False)
        self.assertFalse(s.ir.overlapping)

    def test_within(self):
        s = seq(Sig('a')).within(seq(Sig('b')))
        self.assertIsInstance(s.ir, SeqWithin)

    def test_eventually(self):
        s = seq(Sig('a')).eventually()
        self.assertIsInstance(s.ir, SeqEventually)

    def test_and_operator(self):
        s = seq(Sig('a')) & seq(Sig('b'))
        self.assertIsInstance(s.ir, SeqAnd)

    def test_or_operator(self):
        s = seq(Sig('a')) | seq(Sig('b'))
        self.assertIsInstance(s.ir, SeqOr)

    def test_invert_operator(self):
        s = ~seq(Sig('a'))
        self.assertIsInstance(s.ir, SeqNot)

    def test_chaining(self):
        # req ##1 ack ##2 done
        s = seq(Sig('req')).then(seq(Sig('ack'))).then(seq(Sig('done')), lo=2)
        self.assertIsInstance(s.ir, SeqConcat)
        self.assertIsInstance(s.ir.left, SeqConcat)

    def test_seq_from_seqexpr(self):
        ir = SeqBool(Sig('x'))
        s = seq(ir)
        self.assertIs(s.ir, ir)


# ── SVA string parser ────────────────────────────────────────────────

class TestParseSVA(unittest.TestCase):
    def test_simple_ident(self):
        s = parse_sva('valid')
        self.assertIsInstance(s.ir, SeqBool)
        self.assertEqual(s.ir.expr, Sig('valid'))

    def test_delay_1(self):
        s = parse_sva('a ##1 b')
        self.assertIsInstance(s.ir, SeqConcat)
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, 1)
        self.assertEqual(s.ir.left, SeqBool(Sig('a')))
        self.assertEqual(s.ir.right, SeqBool(Sig('b')))

    def test_delay_range(self):
        s = parse_sva('a ##[1:3] b')
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, 3)

    def test_delay_unbounded(self):
        s = parse_sva('a ##[1:$] b')
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, -1)

    def test_implication_overlapping(self):
        s = parse_sva('req |-> ack')
        self.assertIsInstance(s.ir, SeqImplication)
        self.assertTrue(s.ir.overlapping)
        self.assertEqual(s.ir.antecedent, SeqBool(Sig('req')))
        self.assertEqual(s.ir.consequent, SeqBool(Sig('ack')))

    def test_implication_non_overlapping(self):
        s = parse_sva('req |=> ack')
        self.assertIsInstance(s.ir, SeqImplication)
        self.assertFalse(s.ir.overlapping)

    def test_repeat_exact(self):
        s = parse_sva('a[*3]')
        self.assertIsInstance(s.ir, SeqRepeat)
        self.assertEqual(s.ir.lo, 3)
        self.assertEqual(s.ir.hi, 3)

    def test_repeat_range(self):
        s = parse_sva('a[*1:4]')
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, 4)

    def test_repeat_star(self):
        s = parse_sva('a[*]')
        self.assertEqual(s.ir.lo, 0)
        self.assertEqual(s.ir.hi, -1)

    def test_repeat_plus(self):
        s = parse_sva('a[+]')
        self.assertEqual(s.ir.lo, 1)
        self.assertEqual(s.ir.hi, -1)

    def test_comparison(self):
        s = parse_sva('count == 5')
        self.assertIsInstance(s.ir, SeqBool)
        self.assertIsInstance(s.ir.expr, Compare)
        self.assertEqual(s.ir.expr.op, '==')

    def test_logical_and(self):
        s = parse_sva('valid && !stall')
        self.assertIsInstance(s.ir, SeqBool)
        self.assertIsInstance(s.ir.expr, BoolOp)
        self.assertEqual(s.ir.expr.op, '&&')

    def test_logical_or(self):
        s = parse_sva('a || b')
        self.assertIsInstance(s.ir.expr, BoolOp)
        self.assertEqual(s.ir.expr.op, '||')

    def test_negation(self):
        s = parse_sva('!stall')
        self.assertIsInstance(s.ir.expr, UnaryOp)
        self.assertEqual(s.ir.expr.op, '!')

    def test_parenthesized_sequence(self):
        s = parse_sva('(a ##1 b) |-> c')
        self.assertIsInstance(s.ir, SeqImplication)
        self.assertIsInstance(s.ir.antecedent, SeqConcat)

    def test_chained_delay(self):
        s = parse_sva('a ##1 b ##2 c')
        # Should be left-associative: (a ##1 b) ##2 c
        self.assertIsInstance(s.ir, SeqConcat)
        self.assertEqual(s.ir.lo, 2)
        self.assertIsInstance(s.ir.left, SeqConcat)

    def test_complex_implication(self):
        s = parse_sva('req ##1 valid |-> ack')
        self.assertIsInstance(s.ir, SeqImplication)
        self.assertIsInstance(s.ir.antecedent, SeqConcat)

    def test_syntax_error(self):
        with self.assertRaises(SyntaxError):
            parse_sva('##1')  # delay with no left side

    def test_comparison_ops(self):
        for op in ['!=', '<', '>', '<=', '>=']:
            s = parse_sva(f'x {op} 0')
            self.assertEqual(s.ir.expr.op, op)


# ── Verilog emission ─────────────────────────────────────────────────

class TestTemporalVerilogEmission(unittest.TestCase):
    def _make_ir(self, props):
        ir = IRModule('dut')
        ir.temporal_props = props
        return ir

    def test_no_temporal_no_ifdef(self):
        ir = self._make_ir([])
        v = emit_verilog(ir)
        # Only one `ifdef FORMAL if formal_props also empty
        self.assertNotIn('property', v)

    def test_assert_property_emitted(self):
        tp = TemporalProperty('assert', 'clk', 'posedge',
                              SeqBool(Sig('valid')), 'check_valid')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('`ifdef FORMAL', v)
        self.assertIn('property check_valid_prop', v)
        self.assertIn('@(posedge clk)', v)
        self.assertIn('assert property (check_valid_prop)', v)
        self.assertIn('// check_valid', v)

    def test_cover_property_emitted(self):
        tp = TemporalProperty('cover', 'clk', 'posedge',
                              SeqBool(Sig('done')), 'reaches_done')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('cover property (reaches_done_prop)', v)

    def test_assume_property_emitted(self):
        tp = TemporalProperty('assume', 'clk', 'posedge',
                              SeqBool(Sig('valid')), 'assume_valid')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('assume property (assume_valid_prop)', v)

    def test_seq_concat_emission(self):
        seq_ir = SeqConcat(SeqBool(Sig('req')), SeqBool(Sig('ack')), 1, 1)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'handshake')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('##1', v)

    def test_seq_concat_range_emission(self):
        seq_ir = SeqConcat(SeqBool(Sig('a')), SeqBool(Sig('b')), 1, 3)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('##[1:3]', v)

    def test_seq_concat_unbounded_emission(self):
        seq_ir = SeqConcat(SeqBool(Sig('a')), SeqBool(Sig('b')), 1, -1)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('##[1:$]', v)

    def test_seq_repeat_exact_emission(self):
        seq_ir = SeqRepeat(SeqBool(Sig('a')), 3, 3)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('[*3]', v)

    def test_seq_repeat_star_emission(self):
        seq_ir = SeqRepeat(SeqBool(Sig('a')), 0, -1)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('[*]', v)

    def test_seq_repeat_plus_emission(self):
        seq_ir = SeqRepeat(SeqBool(Sig('a')), 1, -1)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('[+]', v)

    def test_implication_overlapping_emission(self):
        seq_ir = SeqImplication(SeqBool(Sig('req')), SeqBool(Sig('ack')), True)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('|->', v)

    def test_implication_non_overlapping_emission(self):
        seq_ir = SeqImplication(SeqBool(Sig('req')), SeqBool(Sig('ack')), False)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('|=>', v)

    def test_eventually_emission(self):
        seq_ir = SeqEventually(SeqBool(Sig('done')))
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('s_eventually', v)

    def test_within_emission(self):
        seq_ir = SeqWithin(SeqBool(Sig('a')), SeqBool(Sig('b')))
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('within', v)

    def test_and_emission(self):
        seq_ir = SeqAnd(SeqBool(Sig('a')), SeqBool(Sig('b')))
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn(' and ', v)

    def test_or_emission(self):
        seq_ir = SeqOr(SeqBool(Sig('a')), SeqBool(Sig('b')))
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn(' or ', v)

    def test_not_emission(self):
        seq_ir = SeqNot(SeqBool(Sig('a')))
        tp = TemporalProperty('assert', 'clk', 'posedge', seq_ir, 'p')
        ir = self._make_ir([tp])
        v = emit_verilog(ir)
        self.assertIn('not ', v)


# ── .sby generation with temporal properties ─────────────────────────

class TestSbyWithTemporalProps(unittest.TestCase):
    def test_temporal_assert_triggers_bmc(self):
        ir = IRModule('dut')
        ir.temporal_props = [
            TemporalProperty('assert', 'clk', 'posedge',
                             SeqBool(Sig('valid')), 'p')
        ]
        sby = emit_sby(ir)
        self.assertIn('bmc', sby)

    def test_temporal_cover_triggers_cover_task(self):
        ir = IRModule('dut')
        ir.temporal_props = [
            TemporalProperty('cover', 'clk', 'posedge',
                             SeqBool(Sig('done')), 'p')
        ]
        sby = emit_sby(ir)
        self.assertIn('cover', sby)

    def test_mixed_formal_and_temporal(self):
        from veripy.ir import FormalProperty
        ir = IRModule('dut')
        ir.formal_props = [
            FormalProperty('assert', 'clk', 'posedge', Sig('a'), 'fp')
        ]
        ir.temporal_props = [
            TemporalProperty('cover', 'clk', 'posedge', SeqBool(Sig('b')), 'tp')
        ]
        sby = emit_sby(ir)
        self.assertIn('bmc', sby)
        self.assertIn('cover', sby)


# ── Round-trip: parse_sva → emit ─────────────────────────────────────

class TestRoundTrip(unittest.TestCase):
    def _emit(self, sva_str, name='p'):
        s = parse_sva(sva_str)
        ir = IRModule('dut')
        ir.temporal_props = [
            TemporalProperty('assert', 'clk', 'posedge', s.ir, name)
        ]
        return emit_verilog(ir)

    def test_simple_delay_roundtrip(self):
        v = self._emit('req ##1 ack')
        self.assertIn('##1', v)
        self.assertIn('req', v)
        self.assertIn('ack', v)

    def test_implication_roundtrip(self):
        v = self._emit('req |-> ack')
        self.assertIn('|->', v)

    def test_repeat_roundtrip(self):
        v = self._emit('valid[*3]')
        self.assertIn('[*3]', v)


if __name__ == '__main__':
    unittest.main()
