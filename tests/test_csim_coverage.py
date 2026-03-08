"""Tests for csim coverage instrumentation (toggle, line, FSM)."""
import unittest

from veripy.ir import (
    Const, Sig, BinOp, Assign, If,
    ContAssign, CombBlock, SeqBlock,
    Port, RegDecl, IRModule,
)
from veripy.backend_csim import emit_c, CSimModel


def _counter_ir():
    """Simple 4-bit counter IR (no FSM)."""
    return IRModule(
        name='counter',
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
            locals={})],
    )


class TestCoverageEmit(unittest.TestCase):
    """Test that coverage=True emits the expected C symbols."""

    def test_coverage_statics_emitted(self):
        c = emit_c(_counter_ir(), coverage=True)
        self.assertIn('_cov_line', c)
        self.assertIn('_cov_tog_ones', c)
        self.assertIn('_cov_tog_zeros', c)

    def test_coverage_api_functions_emitted(self):
        c = emit_c(_counter_ir(), coverage=True)
        self.assertIn('veripy_cov_n_lines', c)
        self.assertIn('veripy_cov_line', c)
        self.assertIn('veripy_cov_n_sigs', c)
        self.assertIn('veripy_cov_tog_ones', c)
        self.assertIn('veripy_cov_tog_zeros', c)
        self.assertIn('veripy_cov_reset', c)
        self.assertIn('veripy_cov_sig_name', c)

    def test_no_coverage_by_default(self):
        c = emit_c(_counter_ir())
        self.assertNotIn('veripy_cov_n_lines', c)
        self.assertNotIn('_cov_line', c)

    def test_line_counter_incremented(self):
        c = emit_c(_counter_ir(), coverage=True)
        # At least one line counter increment must be emitted
        self.assertIn('_cov_line[0]++', c)

    def test_toggle_tracking_emitted(self):
        c = emit_c(_counter_ir(), coverage=True)
        self.assertIn('_cov_tog_ones', c)
        self.assertIn('_cov_tog_zeros', c)
        self.assertIn('_cv', c)


class TestCoverageRuntime(unittest.TestCase):
    """Test coverage data collected at runtime via CSimModel."""

    def _run_counter(self, n_steps=5):
        with CSimModel(_counter_ir(), coverage=True) as m:
            m.set('reset', 1)
            m.set('enable', 1)
            m.step('clock')
            m.set('reset', 0)
            for _ in range(n_steps):
                m.step('clock')
            return m.get_coverage()

    def test_get_coverage_returns_dict(self):
        cov = self._run_counter()
        self.assertIn('line', cov)
        self.assertIn('toggle', cov)
        self.assertIn('fsm_visited', cov)
        self.assertIn('fsm_trans', cov)

    def test_line_coverage_nonzero(self):
        cov = self._run_counter(n_steps=5)
        counts = [c for _, c in cov['line']]
        self.assertTrue(all(c > 0 for c in counts),
                        f'Expected all blocks executed, got {counts}')

    def test_toggle_coverage_signal_names(self):
        cov = self._run_counter()
        names = [n for n, _, _ in cov['toggle']]
        self.assertIn('cnt', names)
        self.assertIn('count', names)

    def test_toggle_ones_after_counting(self):
        """After counting 1..5, cnt bits 0,1,2 should have been 1."""
        cov = self._run_counter(n_steps=5)
        by_name = {n: (o, z) for n, o, z in cov['toggle']}
        ones, zeros = by_name['cnt']
        # bits 0,1,2 seen as 1 (values 1-5 cover those bits)
        self.assertTrue(ones & 0x7, f'Expected bits 0-2 seen as 1, got ones={hex(ones)}')
        # all 4 bits seen as 0 (reset sets cnt=0)
        self.assertEqual(zeros & 0xF, 0xF, f'Expected all bits seen as 0, got zeros={hex(zeros)}')

    def test_toggle_clock_both_edges(self):
        """Clock should have been both 0 and 1."""
        cov = self._run_counter()
        by_name = {n: (o, z) for n, o, z in cov['toggle']}
        ones, zeros = by_name['clock']
        self.assertEqual(ones & 1, 1)
        self.assertEqual(zeros & 1, 1)

    def test_no_coverage_without_flag(self):
        """get_coverage() returns empty dict when coverage=False."""
        with CSimModel(_counter_ir()) as m:
            m.step('clock')
            self.assertEqual(m.get_coverage(), {})

    def test_reset_coverage(self):
        """reset_coverage() clears all counters."""
        with CSimModel(_counter_ir(), coverage=True) as m:
            m.set('reset', 1)
            m.step('clock')
            m.reset_coverage()
            cov = m.get_coverage()
            counts = [c for _, c in cov['line']]
            self.assertTrue(all(c == 0 for c in counts),
                            f'Expected all zero after reset, got {counts}')

    def test_no_fsm_coverage_for_plain_module(self):
        """Plain counter has no FSM; fsm_visited should be 0."""
        cov = self._run_counter()
        self.assertEqual(cov['fsm_visited'], 0)
        self.assertEqual(cov['fsm_trans'], [])


class TestCoverageWithFSM(unittest.TestCase):
    """Test FSM coverage tracking."""

    def _fsm_ir(self):
        """3-state FSM: IDLE(0) → RUN(1) → DONE(2) → IDLE."""
        from veripy.ir import Case
        # _fsm_state: 2-bit reg; _fsm_next: wire driven by comb
        # seq block: _fsm_state <= _fsm_next on posedge clock (with reset)
        return IRModule(
            name='fsm',
            ports=[Port('clock', 'input', 1), Port('reset', 'input', 1),
                   Port('out', 'output', 2)],
            regs=[RegDecl('_fsm_state', 2)],
            assigns=[ContAssign('out', Sig('_fsm_state'))],
            comb_blocks=[CombBlock(
                stmts=[
                    Assign('_fsm_next', Sig('_fsm_state'), blocking=True),
                    Case(Sig('_fsm_state'), [
                        (Const(0), [Assign('_fsm_next', Const(1), blocking=True)]),
                        (Const(1), [Assign('_fsm_next', Const(2), blocking=True)]),
                        (Const(2), [Assign('_fsm_next', Const(0), blocking=True)]),
                    ], default=[]),
                ],
                locals={'_fsm_next': 2},
            )],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[If(Sig('reset'),
                          [Assign('_fsm_state', Const(0), False)],
                          [Assign('_fsm_state', Sig('_fsm_next'), False)])],
                locals={},
            )],
        )

    def test_fsm_visited_states(self):
        """After cycling through all 3 states, visited bitmask should have bits 0,1,2."""
        with CSimModel(self._fsm_ir(), coverage=True) as m:
            m.set('reset', 1)
            m.step('clock')
            m.set('reset', 0)
            for _ in range(6):   # 2 full cycles
                m.step('clock')
            cov = m.get_coverage()
        visited = cov['fsm_visited']
        self.assertEqual(visited & 0x7, 0x7,
                         f'Expected states 0,1,2 visited, got {bin(visited)}')

    def test_fsm_transitions_recorded(self):
        """Transitions 0→1, 1→2, 2→0 should be recorded."""
        with CSimModel(self._fsm_ir(), coverage=True) as m:
            m.set('reset', 1)
            m.step('clock')
            m.set('reset', 0)
            for _ in range(6):
                m.step('clock')
            cov = m.get_coverage()
        trans = set(cov['fsm_trans'])
        self.assertIn((0, 1), trans)
        self.assertIn((1, 2), trans)
        self.assertIn((2, 0), trans)

    def test_fsm_n_states_nonzero(self):
        c = emit_c(self._fsm_ir(), coverage=True)
        self.assertIn('_cov_fsm_visited', c)
        self.assertIn('_cov_fsm_trans', c)
        self.assertIn('veripy_cov_n_fsm_states', c)


if __name__ == '__main__':
    unittest.main()
