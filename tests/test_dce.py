"""Tests for dead code elimination and constant propagation (dce.py)."""
import unittest

from veripy.ir import (
    Const, Sig, BinOp, UnaryOp, Compare, Mux,
    Assign, If, CombBlock, SeqBlock, IRModule,
    Port, WireDecl, RegDecl,
)
from veripy.dce import const_propagate, dead_code_eliminate, optimize


# ── Helpers ──────────────────────────────────────────────────────────

def _simple_ir(comb_blocks, seq_blocks=None, ports=None, wires=None, regs=None):
    """Build a minimal flat IRModule for testing."""
    return IRModule(
        name='test',
        ports=ports or [Port('in1', 'input', 8), Port('out1', 'output', 8)],
        wires=wires or [],
        regs=regs or [],
        comb_blocks=comb_blocks,
        seq_blocks=seq_blocks or [],
    )


class TestDeadCodeElimination(unittest.TestCase):

    def test_removes_dead_comb_block(self):
        """A comb_block writing to a signal nobody reads should be pruned."""
        ir = _simple_ir(
            wires=[WireDecl('dead', 8), WireDecl('live', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('live', Sig('in1'))]),
                CombBlock(stmts=[Assign('dead', Const(42))]),
                CombBlock(stmts=[Assign('out1', Sig('live'))]),
            ],
        )
        out = dead_code_eliminate(ir)
        targets = [blk.stmts[0].target for blk in out.comb_blocks]
        self.assertIn('live', targets)
        self.assertIn('out1', targets)
        self.assertNotIn('dead', targets)

    def test_preserves_seq_read_signals(self):
        """Signals read by seq_blocks must be kept alive."""
        ir = _simple_ir(
            wires=[WireDecl('w', 8)],
            regs=[RegDecl('r', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('w', Sig('in1'))]),
                CombBlock(stmts=[Assign('out1', Sig('r'))]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'in1')],
                stmts=[Assign('r', Sig('w'), False)],
            )],
        )
        out = dead_code_eliminate(ir)
        targets = [blk.stmts[0].target for blk in out.comb_blocks]
        self.assertIn('w', targets)
        self.assertIn('out1', targets)

    def test_no_pruning_when_all_live(self):
        """When everything is reachable, nothing is removed."""
        ir = _simple_ir(
            comb_blocks=[CombBlock(stmts=[Assign('out1', Sig('in1'))])],
        )
        out = dead_code_eliminate(ir)
        self.assertEqual(len(out.comb_blocks), 1)

    def test_prunes_dead_wire_declarations(self):
        """Wire declarations for dead signals should be removed."""
        ir = _simple_ir(
            wires=[WireDecl('dead', 8), WireDecl('live', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('live', Sig('in1'))]),
                CombBlock(stmts=[Assign('dead', Const(0))]),
                CombBlock(stmts=[Assign('out1', Sig('live'))]),
            ],
        )
        out = dead_code_eliminate(ir)
        wire_names = [w.name for w in out.wires]
        self.assertIn('live', wire_names)
        self.assertNotIn('dead', wire_names)

    def test_transitive_liveness(self):
        """A chain a→b→out1 should keep both a and b alive."""
        ir = _simple_ir(
            wires=[WireDecl('a', 8), WireDecl('b', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('a', Sig('in1'))]),
                CombBlock(stmts=[Assign('b', Sig('a'))]),
                CombBlock(stmts=[Assign('out1', Sig('b'))]),
            ],
        )
        out = dead_code_eliminate(ir)
        self.assertEqual(len(out.comb_blocks), 3)


class TestConstantPropagation(unittest.TestCase):

    def test_substitutes_constant(self):
        """A constant assignment should be substituted into readers."""
        ir = _simple_ir(
            wires=[WireDecl('c', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('c', Const(7))]),
                CombBlock(stmts=[Assign('out1', BinOp('+', Sig('c'), Sig('in1')))]),
            ],
        )
        out = const_propagate(ir)
        # The second block should now have Const(7) instead of Sig('c')
        expr = out.comb_blocks[1].stmts[0].value
        self.assertIsInstance(expr, BinOp)
        self.assertIsInstance(expr.left, Const)
        self.assertEqual(expr.left.value, 7)

    def test_folds_constant_binop(self):
        """Two constants in a BinOp should fold to a single Const."""
        ir = _simple_ir(
            wires=[WireDecl('a', 8), WireDecl('b', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('a', Const(3))]),
                CombBlock(stmts=[Assign('b', Const(4))]),
                CombBlock(stmts=[Assign('out1', BinOp('+', Sig('a'), Sig('b')))]),
            ],
        )
        out = const_propagate(ir)
        expr = out.comb_blocks[2].stmts[0].value
        self.assertIsInstance(expr, Const)
        self.assertEqual(expr.value, 7)

    def test_no_propagation_for_seq_written(self):
        """Signals written by seq_blocks should not be propagated."""
        ir = _simple_ir(
            regs=[RegDecl('r', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('r', Const(0))]),
                CombBlock(stmts=[Assign('out1', Sig('r'))]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'in1')],
                stmts=[Assign('r', Const(1), False)],
            )],
        )
        out = const_propagate(ir)
        # r should NOT be substituted since seq_block also writes it
        expr = out.comb_blocks[1].stmts[0].value
        self.assertIsInstance(expr, Sig)
        self.assertEqual(expr.name, 'r')

    def test_no_propagation_for_multi_writer(self):
        """Signals written by multiple comb_blocks should not be propagated."""
        ir = _simple_ir(
            wires=[WireDecl('w', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('w', Const(1))]),
                CombBlock(stmts=[Assign('w', Const(2))]),
                CombBlock(stmts=[Assign('out1', Sig('w'))]),
            ],
        )
        out = const_propagate(ir)
        expr = out.comb_blocks[2].stmts[0].value
        self.assertIsInstance(expr, Sig)

    def test_folds_mux_with_const_sel(self):
        """Mux with constant selector should fold to the selected branch."""
        ir = _simple_ir(
            wires=[WireDecl('sel', 1)],
            comb_blocks=[
                CombBlock(stmts=[Assign('sel', Const(1))]),
                CombBlock(stmts=[Assign('out1',
                    Mux(Sig('sel'), Sig('in1'), Const(0)))]),
            ],
        )
        out = const_propagate(ir)
        expr = out.comb_blocks[1].stmts[0].value
        self.assertIsInstance(expr, Sig)
        self.assertEqual(expr.name, 'in1')

    def test_folds_if_with_const_cond(self):
        """If with constant condition should eliminate dead branch."""
        ir = _simple_ir(
            wires=[WireDecl('c', 1)],
            comb_blocks=[
                CombBlock(stmts=[Assign('c', Const(0))]),
                CombBlock(stmts=[If(Sig('c'),
                    [Assign('out1', Const(1))],
                    [Assign('out1', Const(2))])]),
            ],
        )
        out = const_propagate(ir)
        # The If should be replaced with the else branch
        stmts = out.comb_blocks[1].stmts
        self.assertEqual(len(stmts), 1)
        self.assertIsInstance(stmts[0], Assign)
        self.assertEqual(stmts[0].value.value, 2)


class TestOptimize(unittest.TestCase):

    def test_const_prop_enables_dce(self):
        """Constant propagation should make the constant-source block dead."""
        ir = _simple_ir(
            wires=[WireDecl('c', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('c', Const(5))]),
                CombBlock(stmts=[Assign('out1', BinOp('+', Sig('c'), Sig('in1')))]),
            ],
        )
        out = optimize(ir)
        # After const prop, 'c' is substituted everywhere.
        # After DCE, the block assigning 'c' should be dead.
        targets = [blk.stmts[0].target for blk in out.comb_blocks]
        self.assertNotIn('c', targets)
        self.assertIn('out1', targets)

    def test_empty_ir_unchanged(self):
        """An IR with no comb_blocks should pass through unchanged."""
        ir = _simple_ir(comb_blocks=[])
        out = optimize(ir)
        self.assertEqual(len(out.comb_blocks), 0)


if __name__ == '__main__':
    unittest.main()
