"""Tests for WGSL GPU backend: Concat, Mem, and sub-module flattening."""
import unittest

from veripy.ir import (
    Const, Sig, BinOp, Slice, Index, Concat,
    Assign, If, MemWrite, ContAssign, CombBlock, SeqBlock,
    Port, WireDecl, RegDecl, MemDecl, Instance, IRModule,
)
from veripy.backend_wgpu import emit_wgsl, _expr_width, _expr
from veripy.flatten import flatten_ir


class TestConcat(unittest.TestCase):
    def test_two_slices(self):
        """out = {a[3:0], b[3:0]} → shift-and-or."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('out', 'output', 8)],
            assigns=[ContAssign('out', Concat([
                Slice(Sig('a'), Const(3), Const(0)),
                Slice(Sig('b'), Const(3), Const(0)),
            ]))])
        wgsl, _, _ = emit_wgsl(ir)
        self.assertIn('<<', wgsl)
        self.assertIn('|', wgsl)

    def test_three_parts(self):
        """out = {a, b, c} with 1-bit signals."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 1), Port('b', 'input', 1),
                   Port('c', 'input', 1), Port('out', 'output', 3)],
            assigns=[ContAssign('out', Concat([Sig('a'), Sig('b'), Sig('c')]))])
        wgsl, _, _ = emit_wgsl(ir)
        # a shifted by 2, b shifted by 1, c at bit 0
        self.assertIn('<< 2u', wgsl)
        self.assertIn('<< 1u', wgsl)

    def test_expr_width_concat(self):
        sig_w = {'x': 4, 'y': 4}
        node = Concat([Sig('x'), Sig('y')])
        self.assertEqual(_expr_width(node, sig_w), 8)


class TestMem(unittest.TestCase):
    def _make_mem_ir(self):
        return IRModule(name='t',
            ports=[Port('clock', 'input', 1), Port('wen', 'input', 1),
                   Port('waddr', 'input', 2), Port('wdata', 'input', 8),
                   Port('raddr', 'input', 2), Port('rdata', 'output', 8)],
            mems=[MemDecl('buf', 4, 8)],
            assigns=[ContAssign('rdata', Index(Sig('buf'), Sig('raddr')))],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[If(Sig('wen'),
                          [MemWrite('buf', Sig('waddr'), Sig('wdata'), blocking=False)],
                          [])],
                locals={},
            )])

    def test_mem_array_in_struct(self):
        wgsl, _, _ = emit_wgsl(self._make_mem_ir())
        self.assertIn('s_buf: array<u32, 4>,', wgsl)

    def test_mem_read(self):
        wgsl, _, _ = emit_wgsl(self._make_mem_ir())
        self.assertIn('s_buf[', wgsl)
        # Should NOT use bit-shift for mem reads
        self.assertNotIn('>> (*s).s_raddr', wgsl)

    def test_mem_write(self):
        wgsl, _, _ = emit_wgsl(self._make_mem_ir())
        self.assertIn('s_buf[', wgsl)
        self.assertIn('wmask(snap.s_wdata, 8u)', wgsl)

    def test_mem_layout_returned(self):
        _, _, mems = emit_wgsl(self._make_mem_ir())
        self.assertEqual(mems, [('buf', 4, 8)])


class TestFlatten(unittest.TestCase):
    def _make_counter(self):
        return IRModule(name='counter',
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
            )])

    def _make_wrapper(self):
        return IRModule(name='wrapper',
            ports=[Port('clock', 'input', 1), Port('reset', 'input', 1),
                   Port('enable', 'input', 1), Port('out', 'output', 4)],
            wires=[WireDecl('c0_clock', 1), WireDecl('c0_count', 4),
                   WireDecl('c0_reset', 1), WireDecl('c0_enable', 1)],
            assigns=[
                ContAssign('c0_clock', Sig('clock')),
                ContAssign('c0_reset', Sig('reset')),
                ContAssign('c0_enable', Sig('enable')),
                ContAssign('out', Sig('c0_count')),
            ],
            instances=[Instance('counter', 'c0', {},
                [('clock', 'c0_clock'), ('reset', 'c0_reset'),
                 ('enable', 'c0_enable'), ('count', 'c0_count')])])

    def test_instances_removed(self):
        flat = flatten_ir(self._make_wrapper(), {'counter': self._make_counter()})
        self.assertEqual(flat.instances, [])

    def test_child_reg_prefixed(self):
        flat = flatten_ir(self._make_wrapper(), {'counter': self._make_counter()})
        reg_names = [r.name for r in flat.regs]
        self.assertIn('c0_cnt', reg_names)

    def test_child_seq_block_inlined(self):
        flat = flatten_ir(self._make_wrapper(), {'counter': self._make_counter()})
        # Parent had no seq blocks; child had one
        self.assertEqual(len(flat.seq_blocks), 1)
        # Edge should reference the parent wire, not bare 'clock'
        self.assertEqual(flat.seq_blocks[0].edges, [('posedge', 'c0_clock')])

    def test_flattened_emits_valid_wgsl(self):
        flat = flatten_ir(self._make_wrapper(), {'counter': self._make_counter()})
        wgsl, names, _ = emit_wgsl(flat)
        self.assertIn('s_c0_cnt', wgsl)
        self.assertIn('c0_cnt', names)

    def test_unknown_child_raises(self):
        with self.assertRaises(ValueError):
            flatten_ir(self._make_wrapper(), {})

    def test_child_mem_flattened(self):
        child = IRModule(name='mem_mod',
            ports=[Port('clock', 'input', 1), Port('addr', 'input', 2),
                   Port('rdata', 'output', 8)],
            mems=[MemDecl('ram', 4, 8)],
            assigns=[ContAssign('rdata', Index(Sig('ram'), Sig('addr')))])
        parent = IRModule(name='top',
            ports=[Port('clock', 'input', 1), Port('addr', 'input', 2),
                   Port('out', 'output', 8)],
            wires=[WireDecl('m_clock', 1), WireDecl('m_addr', 2),
                   WireDecl('m_rdata', 8)],
            assigns=[
                ContAssign('m_clock', Sig('clock')),
                ContAssign('m_addr', Sig('addr')),
                ContAssign('out', Sig('m_rdata')),
            ],
            instances=[Instance('mem_mod', 'm', {},
                [('clock', 'm_clock'), ('addr', 'm_addr'), ('rdata', 'm_rdata')])])
        flat = flatten_ir(parent, {'mem_mod': child})
        mem_names = [m.name for m in flat.mems]
        self.assertIn('m_ram', mem_names)
        wgsl, _, mems = emit_wgsl(flat)
        self.assertIn('s_m_ram: array<u32, 4>,', wgsl)


if __name__ == '__main__':
    unittest.main()
