"""Tests for SVA temporal property FSM compilation in csim backend (#118, #119)."""
import unittest

from veripy.ir import (
    Const, Sig, Compare, BoolOp,
    Assign, If, CombBlock, SeqBlock,
    Port, RegDecl, IRModule,
    SeqBool, SeqConcat, SeqRepeat, SeqImplication, TemporalProperty,
)
from veripy.backend_csim import emit_c, CSimModel, _seq_to_steps


# ── _seq_to_steps unit tests ─────────────────────────────────────────

class TestSeqToSteps(unittest.TestCase):
    def test_seq_bool(self):
        steps = _seq_to_steps(SeqBool(Sig('a')))
        self.assertEqual(steps, [(Sig('a'), 0)])

    def test_seq_concat_exact(self):
        steps = _seq_to_steps(SeqConcat(SeqBool(Sig('a')), SeqBool(Sig('b')), 1, 1))
        self.assertEqual(steps, [(Sig('a'), 0), (Sig('b'), 1)])

    def test_seq_concat_delay_2(self):
        steps = _seq_to_steps(SeqConcat(SeqBool(Sig('a')), SeqBool(Sig('b')), 2, 2))
        self.assertEqual(steps, [(Sig('a'), 0), (Sig('b'), 2)])

    def test_seq_concat_range_returns_none(self):
        steps = _seq_to_steps(SeqConcat(SeqBool(Sig('a')), SeqBool(Sig('b')), 1, 3))
        self.assertIsNone(steps)

    def test_seq_concat_chained(self):
        # a ##1 b ##2 c
        inner = SeqConcat(SeqBool(Sig('a')), SeqBool(Sig('b')), 1, 1)
        outer = SeqConcat(inner, SeqBool(Sig('c')), 2, 2)
        steps = _seq_to_steps(outer)
        self.assertEqual(steps, [(Sig('a'), 0), (Sig('b'), 1), (Sig('c'), 2)])

    def test_seq_repeat_exact(self):
        steps = _seq_to_steps(SeqRepeat(SeqBool(Sig('a')), 3, 3))
        self.assertEqual(steps, [(Sig('a'), 0), (Sig('a'), 0), (Sig('a'), 0)])

    def test_seq_repeat_range_returns_none(self):
        steps = _seq_to_steps(SeqRepeat(SeqBool(Sig('a')), 1, 3))
        self.assertIsNone(steps)

    def test_seq_repeat_zero_returns_none(self):
        steps = _seq_to_steps(SeqRepeat(SeqBool(Sig('a')), 0, 0))
        self.assertIsNone(steps)


# ── C code generation tests ──────────────────────────────────────────

def _make_ir(temporal_props):
    """Minimal IR with clock/reset/valid signals and given temporal props."""
    return IRModule(
        name='dut',
        ports=[Port('clk', 'input', 1), Port('valid', 'input', 1),
               Port('ack', 'input', 1)],
        regs=[],
        assigns=[],
        seq_blocks=[SeqBlock(edges=[('posedge', 'clk')], stmts=[], locals={})],
        temporal_props=temporal_props,
    )


class TestTemporalCCodeGen(unittest.TestCase):
    def test_seq_bool_assert_emits_check(self):
        tp = TemporalProperty('assert', 'clk', 'posedge', SeqBool(Sig('valid')), 'p')
        c = emit_c(_make_ir([tp]))
        self.assertIn('_assert_fail = 1', c)
        self.assertIn('_assert_fail_prop', c)
        self.assertIn('_assert_fail_cycle', c)

    def test_seq_concat_emits_pending_array(self):
        seq = SeqConcat(SeqBool(Sig('valid')), SeqBool(Sig('ack')), 1, 1)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq, 'p')
        c = emit_c(_make_ir([tp]))
        self.assertIn('_seq_p0_step', c)
        self.assertIn('_seq_p0_dly', c)
        self.assertIn('_seq_p0_n', c)

    def test_implication_emits_pending_array(self):
        seq = SeqImplication(SeqBool(Sig('valid')), SeqBool(Sig('ack')), True)
        tp = TemporalProperty('assert', 'clk', 'posedge', seq, 'p')
        c = emit_c(_make_ir([tp]))
        self.assertIn('_seq_p0_step', c)

    def test_assert_info_function_emitted(self):
        tp = TemporalProperty('assert', 'clk', 'posedge', SeqBool(Sig('valid')), 'p')
        c = emit_c(_make_ir([tp]))
        self.assertIn('veripy_assert_info', c)

    def test_eval_cycle_counter_emitted(self):
        tp = TemporalProperty('assert', 'clk', 'posedge', SeqBool(Sig('valid')), 'p')
        c = emit_c(_make_ir([tp]))
        self.assertIn('_eval_cycle', c)


# ── Runtime simulation tests ─────────────────────────────────────────

def _make_runtime_ir():
    """IR: clk, valid, ack inputs; posedge clk seq block."""
    return IRModule(
        name='dut',
        ports=[Port('clk', 'input', 1), Port('valid', 'input', 1),
               Port('ack', 'input', 1)],
        regs=[],
        assigns=[],
        seq_blocks=[SeqBlock(edges=[('posedge', 'clk')], stmts=[], locals={})],
    )


def _tick(m, clk_name='clk'):
    m.set(clk_name, 0); m.eval()
    m.set(clk_name, 1); m.eval()


class TestSeqBoolRuntime(unittest.TestCase):
    def setUp(self):
        ir = _make_runtime_ir()
        ir.temporal_props = [
            TemporalProperty('assert', 'clk', 'posedge', SeqBool(Sig('valid')), 'p')
        ]
        self.m = CSimModel(ir)

    def tearDown(self):
        self.m.close()

    def test_no_failure_when_valid_high(self):
        self.m.set('valid', 1); self.m.set('ack', 0)
        _tick(self.m)
        self.assertFalse(self.m.assert_failed())

    def test_failure_when_valid_low(self):
        self.m.set('valid', 0); self.m.set('ack', 0)
        _tick(self.m)
        self.assertTrue(self.m.assert_failed())

    def test_assert_info_prop_index(self):
        self.m.set('valid', 0)
        _tick(self.m)
        prop_idx, cycle = self.m.assert_info()
        self.assertEqual(prop_idx, 0)
        self.assertGreater(cycle, 0)

    def test_assert_clear_resets_info(self):
        self.m.set('valid', 0)
        _tick(self.m)
        self.m.assert_clear()
        prop_idx, cycle = self.m.assert_info()
        self.assertEqual(prop_idx, -1)
        self.assertEqual(cycle, 0)


class TestSeqConcatRuntime(unittest.TestCase):
    """valid ##1 ack: when valid is high, ack must be high next cycle."""

    def setUp(self):
        ir = _make_runtime_ir()
        seq = SeqConcat(SeqBool(Sig('valid')), SeqBool(Sig('ack')), 1, 1)
        ir.temporal_props = [
            TemporalProperty('assert', 'clk', 'posedge', seq, 'p')
        ]
        self.m = CSimModel(ir)

    def tearDown(self):
        self.m.close()

    def test_no_failure_when_ack_follows(self):
        self.m.set('valid', 1); self.m.set('ack', 0); _tick(self.m)  # valid high
        self.m.set('valid', 0); self.m.set('ack', 1); _tick(self.m)  # ack high next cycle
        self.assertFalse(self.m.assert_failed())

    def test_failure_when_ack_missing(self):
        self.m.set('valid', 1); self.m.set('ack', 0); _tick(self.m)  # valid high
        self.m.set('valid', 0); self.m.set('ack', 0); _tick(self.m)  # ack missing
        self.assertTrue(self.m.assert_failed())

    def test_no_failure_when_valid_never_high(self):
        self.m.set('valid', 0); self.m.set('ack', 0)
        for _ in range(5):
            _tick(self.m)
        self.assertFalse(self.m.assert_failed())


class TestSeqImplicationRuntime(unittest.TestCase):
    """valid |-> ack: whenever valid is high, ack must also be high."""

    def setUp(self):
        ir = _make_runtime_ir()
        seq = SeqImplication(SeqBool(Sig('valid')), SeqBool(Sig('ack')), True)
        ir.temporal_props = [
            TemporalProperty('assert', 'clk', 'posedge', seq, 'p')
        ]
        self.m = CSimModel(ir)

    def tearDown(self):
        self.m.close()

    def test_no_failure_when_both_high(self):
        self.m.set('valid', 1); self.m.set('ack', 1)
        _tick(self.m)
        self.assertFalse(self.m.assert_failed())

    def test_failure_when_valid_high_ack_low(self):
        self.m.set('valid', 1); self.m.set('ack', 0)
        _tick(self.m)
        self.assertTrue(self.m.assert_failed())

    def test_no_failure_when_valid_low(self):
        self.m.set('valid', 0); self.m.set('ack', 0)
        _tick(self.m)
        self.assertFalse(self.m.assert_failed())


class TestSeqImplicationDelayedRuntime(unittest.TestCase):
    """valid |-> ##1 ack: when valid, ack must be high one cycle later."""

    def setUp(self):
        ir = _make_runtime_ir()
        cons = SeqConcat(SeqBool(Sig('valid')), SeqBool(Sig('ack')), 1, 1)
        seq = SeqImplication(SeqBool(Sig('valid')), cons, True)
        ir.temporal_props = [
            TemporalProperty('assert', 'clk', 'posedge', seq, 'p')
        ]
        self.m = CSimModel(ir)

    def tearDown(self):
        self.m.close()

    def test_no_failure_when_ack_follows(self):
        self.m.set('valid', 1); self.m.set('ack', 0); _tick(self.m)
        self.m.set('valid', 0); self.m.set('ack', 1); _tick(self.m)
        self.assertFalse(self.m.assert_failed())

    def test_failure_when_ack_missing(self):
        self.m.set('valid', 1); self.m.set('ack', 0); _tick(self.m)
        self.m.set('valid', 0); self.m.set('ack', 0); _tick(self.m)
        self.assertTrue(self.m.assert_failed())


if __name__ == '__main__':
    unittest.main()
