"""Tests for recursive flatten_ir and topo_sort_comb."""
import unittest

from veripy.ir import (
    Const, Sig, BinOp, Compare,
    Assign, If, ContAssign, CombBlock, SeqBlock, InitialBlock, AlwaysBlock,
    FormalProperty,
    Port, WireDecl, RegDecl, MemDecl, DualPortMemDecl, TrueDualPortMemDecl,
    Instance, IRModule,
)
from veripy.flatten import flatten_ir, topo_sort_comb


# ── Helpers ──────────────────────────────────────────────────────────

def _make_leaf():
    """Simple leaf module: counter with no sub-instances."""
    return IRModule(name='leaf',
        ports=[Port('clock', 'input', 1), Port('enable', 'input', 1),
               Port('count', 'output', 4)],
        regs=[RegDecl('cnt', 4)],
        assigns=[ContAssign('count', Sig('cnt'))],
        seq_blocks=[SeqBlock(
            edges=[('posedge', 'clock')],
            stmts=[If(Sig('enable'),
                      [Assign('cnt', BinOp('+', Sig('cnt'), Const(1)), False)],
                      [])],
            locals={})])


def _make_mid(leaf_type='leaf'):
    """Mid-level module that instantiates a leaf."""
    return IRModule(name='mid',
        ports=[Port('clock', 'input', 1), Port('enable', 'input', 1),
               Port('out', 'output', 4)],
        wires=[WireDecl('l_clock', 1), WireDecl('l_enable', 1),
               WireDecl('l_count', 4)],
        assigns=[
            ContAssign('l_clock', Sig('clock')),
            ContAssign('l_enable', Sig('enable')),
            ContAssign('out', Sig('l_count')),
        ],
        instances=[Instance(leaf_type, 'l', {},
            [('clock', 'l_clock'), ('enable', 'l_enable'),
             ('count', 'l_count')])])


def _make_top(mid_type='mid'):
    """Top-level module that instantiates a mid."""
    return IRModule(name='top',
        ports=[Port('clock', 'input', 1), Port('enable', 'input', 1),
               Port('result', 'output', 4)],
        wires=[WireDecl('m_clock', 1), WireDecl('m_enable', 1),
               WireDecl('m_out', 4)],
        assigns=[
            ContAssign('m_clock', Sig('clock')),
            ContAssign('m_enable', Sig('enable')),
            ContAssign('result', Sig('m_out')),
        ],
        instances=[Instance(mid_type, 'm', {},
            [('clock', 'm_clock'), ('enable', 'm_enable'),
             ('out', 'm_out')])])


# ── Recursive flatten tests ─────────────────────────────────────────

class TestRecursiveFlatten(unittest.TestCase):
    def test_two_level_hierarchy(self):
        """top → mid → leaf should flatten to zero instances."""
        registry = {'mid': _make_mid(), 'leaf': _make_leaf()}
        flat = flatten_ir(_make_top(), registry)
        self.assertEqual(flat.instances, [])

    def test_deep_reg_prefixed(self):
        """Leaf's 'cnt' reg should become 'm_l_cnt' after two levels."""
        registry = {'mid': _make_mid(), 'leaf': _make_leaf()}
        flat = flatten_ir(_make_top(), registry)
        reg_names = [r.name for r in flat.regs]
        self.assertIn('m_l_cnt', reg_names)

    def test_deep_seq_block_edges(self):
        """Leaf's seq block edge should reference 'm_l_clock'."""
        registry = {'mid': _make_mid(), 'leaf': _make_leaf()}
        flat = flatten_ir(_make_top(), registry)
        edges = [e for blk in flat.seq_blocks for e in blk.edges]
        self.assertIn(('posedge', 'm_l_clock'), edges)


class TestFlattenInitialBlock(unittest.TestCase):
    def test_initial_block_inlined(self):
        child = IRModule(name='child',
            ports=[Port('x', 'input', 1)],
            initial_blocks=[InitialBlock(
                stmts=[Assign('x', Const(0))])])
        parent = IRModule(name='top',
            ports=[Port('x', 'input', 1)],
            wires=[WireDecl('c_x', 1)],
            assigns=[ContAssign('c_x', Sig('x'))],
            instances=[Instance('child', 'c', {},
                [('x', 'c_x')])])
        flat = flatten_ir(parent, {'child': child})
        self.assertEqual(len(flat.initial_blocks), 1)
        stmt = flat.initial_blocks[0].stmts[0]
        self.assertEqual(stmt.target, 'c_x')


class TestFlattenAlwaysBlock(unittest.TestCase):
    def test_always_block_inlined(self):
        child = IRModule(name='child',
            ports=[Port('a', 'input', 1), Port('b', 'output', 1)],
            always_blocks=[AlwaysBlock(
                stmts=[Assign('b', Sig('a'))])])
        parent = IRModule(name='top',
            ports=[Port('a', 'input', 1), Port('out', 'output', 1)],
            wires=[WireDecl('c_a', 1), WireDecl('c_b', 1)],
            assigns=[ContAssign('c_a', Sig('a')), ContAssign('out', Sig('c_b'))],
            instances=[Instance('child', 'c', {},
                [('a', 'c_a'), ('b', 'c_b')])])
        flat = flatten_ir(parent, {'child': child})
        self.assertEqual(len(flat.always_blocks), 1)
        stmt = flat.always_blocks[0].stmts[0]
        self.assertEqual(stmt.target, 'c_b')


class TestFlattenFormalProp(unittest.TestCase):
    def test_formal_prop_inlined(self):
        child = IRModule(name='child',
            ports=[Port('clock', 'input', 1), Port('x', 'input', 1)],
            formal_props=[FormalProperty(
                kind='assert', clock='clock', edge='posedge',
                expr=Sig('x'), name='check_x')])
        parent = IRModule(name='top',
            ports=[Port('clock', 'input', 1), Port('x', 'input', 1)],
            wires=[WireDecl('c_clock', 1), WireDecl('c_x', 1)],
            assigns=[ContAssign('c_clock', Sig('clock')),
                     ContAssign('c_x', Sig('x'))],
            instances=[Instance('child', 'c', {},
                [('clock', 'c_clock'), ('x', 'c_x')])])
        flat = flatten_ir(parent, {'child': child})
        self.assertEqual(len(flat.formal_props), 1)
        fp = flat.formal_props[0]
        self.assertEqual(fp.clock, 'c_clock')
        self.assertEqual(fp.name, 'c_check_x')
        self.assertEqual(fp.expr.name, 'c_x')


class TestFlattenDualPortMem(unittest.TestCase):
    def test_dual_port_mem_signals_renamed(self):
        child = IRModule(name='child',
            ports=[Port('clk', 'input', 1), Port('we', 'input', 1),
                   Port('wa', 'input', 2), Port('wd', 'input', 8),
                   Port('ra', 'input', 2), Port('rd', 'output', 8)],
            mems=[DualPortMemDecl('ram', 4, 8, clock='clk', we='we',
                                  waddr='wa', wdata='wd', raddr='ra', rdata='rd')])
        parent = IRModule(name='top',
            ports=[Port('clk', 'input', 1), Port('we', 'input', 1),
                   Port('wa', 'input', 2), Port('wd', 'input', 8),
                   Port('ra', 'input', 2), Port('rd', 'output', 8)],
            wires=[WireDecl('m_clk', 1), WireDecl('m_we', 1),
                   WireDecl('m_wa', 2), WireDecl('m_wd', 8),
                   WireDecl('m_ra', 2), WireDecl('m_rd', 8)],
            assigns=[
                ContAssign('m_clk', Sig('clk')), ContAssign('m_we', Sig('we')),
                ContAssign('m_wa', Sig('wa')), ContAssign('m_wd', Sig('wd')),
                ContAssign('m_ra', Sig('ra')), ContAssign('rd', Sig('m_rd')),
            ],
            instances=[Instance('child', 'm', {},
                [('clk', 'm_clk'), ('we', 'm_we'), ('wa', 'm_wa'),
                 ('wd', 'm_wd'), ('ra', 'm_ra'), ('rd', 'm_rd')])])
        flat = flatten_ir(parent, {'child': child})
        dp = [m for m in flat.mems if isinstance(m, DualPortMemDecl)]
        self.assertEqual(len(dp), 1)
        self.assertEqual(dp[0].name, 'm_ram')
        self.assertEqual(dp[0].clock, 'm_clk')
        self.assertEqual(dp[0].rdata, 'm_rd')


# ── Topological sort tests ──────────────────────────────────────────

class TestTopoSort(unittest.TestCase):
    def test_already_sorted(self):
        """a = 1; b = a  →  order preserved."""
        mod = IRModule(name='t',
            ports=[Port('b', 'output', 1)],
            wires=[WireDecl('a', 1)],
            assigns=[ContAssign('a', Const(1)),
                     ContAssign('b', Sig('a'))])
        out = topo_sort_comb(mod)
        targets = [a.target for a in out.assigns]
        self.assertEqual(targets, ['a', 'b'])

    def test_reverse_order_sorted(self):
        """b = a; a = 1  →  reordered to a, b."""
        mod = IRModule(name='t',
            ports=[Port('b', 'output', 1)],
            wires=[WireDecl('a', 1)],
            assigns=[ContAssign('b', Sig('a')),
                     ContAssign('a', Const(1))])
        out = topo_sort_comb(mod)
        targets = [a.target for a in out.assigns]
        self.assertLess(targets.index('a'), targets.index('b'))

    def test_chain_of_three(self):
        """c = b; b = a; a = in  →  a, b, c."""
        mod = IRModule(name='t',
            ports=[Port('inp', 'input', 1), Port('c', 'output', 1)],
            wires=[WireDecl('a', 1), WireDecl('b', 1)],
            assigns=[ContAssign('c', Sig('b')),
                     ContAssign('b', Sig('a')),
                     ContAssign('a', Sig('inp'))])
        out = topo_sort_comb(mod)
        targets = [a.target for a in out.assigns]
        self.assertLess(targets.index('a'), targets.index('b'))
        self.assertLess(targets.index('b'), targets.index('c'))

    def test_comb_block_sorted(self):
        """CombBlock writing 'b' depends on ContAssign writing 'a'."""
        mod = IRModule(name='t',
            ports=[Port('b', 'output', 1)],
            wires=[WireDecl('a', 1)],
            assigns=[ContAssign('a', Const(1))],
            comb_blocks=[CombBlock(stmts=[Assign('b', Sig('a'))], locals={})])
        out = topo_sort_comb(mod)
        # 'a' assign should come before 'b' comb block
        self.assertEqual(len(out.assigns), 1)
        self.assertEqual(out.assigns[0].target, 'a')
        self.assertEqual(len(out.comb_blocks), 1)

    def test_loop_raises(self):
        """a = b; b = a  →  ValueError."""
        mod = IRModule(name='t',
            wires=[WireDecl('a', 1), WireDecl('b', 1)],
            assigns=[ContAssign('a', Sig('b')),
                     ContAssign('b', Sig('a'))])
        with self.assertRaises(ValueError):
            topo_sort_comb(mod)

    def test_no_comb_passthrough(self):
        """Module with no assigns/comb_blocks returns copy unchanged."""
        mod = IRModule(name='t',
            ports=[Port('clock', 'input', 1)],
            seq_blocks=[SeqBlock(edges=[('posedge', 'clock')], stmts=[])])
        out = topo_sort_comb(mod)
        self.assertEqual(len(out.seq_blocks), 1)

    def test_independent_nodes_stable(self):
        """Independent assigns (no deps) should not crash."""
        mod = IRModule(name='t',
            ports=[Port('x', 'input', 1), Port('y', 'input', 1)],
            wires=[WireDecl('a', 1), WireDecl('b', 1)],
            assigns=[ContAssign('a', Sig('x')),
                     ContAssign('b', Sig('y'))])
        out = topo_sort_comb(mod)
        self.assertEqual(len(out.assigns), 2)


if __name__ == '__main__':
    unittest.main()
