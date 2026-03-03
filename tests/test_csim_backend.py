"""Tests for native C simulation backend (backend_csim)."""
import unittest

from veripy.ir import (
    Const, Sig, BinOp, UnaryOp, Compare, Mux, Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock,
    Port, WireDecl, RegDecl, MemDecl, IRModule,
)
from veripy.backend_csim import emit_c, CSimModel, _count_stmts, _INLINE_THRESHOLD


class TestEmitC(unittest.TestCase):
    """Test C code generation (string output, no compilation)."""

    def _counter_ir(self):
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
                locals={})])

    def test_struct_has_signals(self):
        c = emit_c(self._counter_ir())
        self.assertIn('uint8_t clock;', c)
        self.assertIn('uint8_t count;', c)
        self.assertIn('uint8_t cnt;', c)

    def test_prev_clock(self):
        c = emit_c(self._counter_ir())
        self.assertIn('_prev_clock', c)

    def test_posedge_detection(self):
        c = emit_c(self._counter_ir())
        self.assertIn('s->clock && !s->_prev_clock', c)

    def test_set_get_functions(self):
        c = emit_c(self._counter_ir())
        self.assertIn('veripy_set_clock', c)
        self.assertIn('veripy_get_count', c)
        # Outputs should not have setters
        self.assertNotIn('veripy_set_count', c)

    def test_create_destroy(self):
        c = emit_c(self._counter_ir())
        self.assertIn('veripy_create', c)
        self.assertIn('veripy_destroy', c)

    def test_comb_assign(self):
        c = emit_c(self._counter_ir())
        self.assertIn('s->count', c)
        self.assertIn('s->cnt', c)

    def test_wider_signal_type(self):
        ir = IRModule(name='wide',
            ports=[Port('data', 'input', 16), Port('out', 'output', 32)],
            assigns=[ContAssign('out', Sig('data'))])
        c = emit_c(ir)
        self.assertIn('uint16_t data;', c)
        self.assertIn('uint32_t out;', c)

    def test_mem_array(self):
        ir = IRModule(name='mem_test',
            ports=[Port('addr', 'input', 2), Port('rdata', 'output', 8)],
            mems=[MemDecl('buf', 4, 8)],
            assigns=[ContAssign('rdata', Index(Sig('buf'), Sig('addr')))])
        c = emit_c(ir)
        self.assertIn('uint8_t buf[4];', c)

    def test_mux_expr(self):
        ir = IRModule(name='mux_test',
            ports=[Port('sel', 'input', 1), Port('a', 'input', 8),
                   Port('b', 'input', 8), Port('out', 'output', 8)],
            assigns=[ContAssign('out', Mux(Sig('sel'), Sig('a'), Sig('b')))])
        c = emit_c(ir)
        self.assertIn('?', c)

    def test_slice_expr(self):
        ir = IRModule(name='slice_test',
            ports=[Port('data', 'input', 8), Port('out', 'output', 4)],
            assigns=[ContAssign('out', Slice(Sig('data'), Const(3), Const(0)))])
        c = emit_c(ir)
        self.assertIn('>>', c)

    def test_case_stmt(self):
        ir = IRModule(name='case_test',
            ports=[Port('sel', 'input', 2), Port('out', 'output', 8)],
            comb_blocks=[CombBlock(
                stmts=[Case(Sig('sel'),
                    [(Const(0), [Assign('out', Const(10))]),
                     (Const(1), [Assign('out', Const(20))])],
                    [Assign('out', Const(0))])],
                locals={})])
        c = emit_c(ir)
        self.assertIn('switch', c)
        self.assertIn('case', c)
        self.assertIn('default', c)

    def test_per_block_cont_assigns(self):
        """Continuous assigns get their own static function."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 8), Port('out', 'output', 8)],
            assigns=[ContAssign('out', Sig('a'))])
        c = emit_c(ir)
        self.assertIn('static', c)
        self.assertIn('_cont_assigns(State* s)', c)
        self.assertIn('_cont_assigns(s);', c)

    def test_per_block_comb(self):
        """Each comb_block gets _comb_N()."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 8), Port('out', 'output', 8)],
            comb_blocks=[CombBlock(
                stmts=[Assign('out', Sig('a'))], locals={})])
        c = emit_c(ir)
        self.assertIn('_comb_0(State* s)', c)
        self.assertIn('_comb_0(s);', c)

    def test_per_block_seq(self):
        """Each seq_block gets _seq_N()."""
        c = emit_c(self._counter_ir())
        self.assertIn('_seq_0(State* s)', c)
        self.assertIn('_seq_0(s);', c)

    def test_eval_calls_not_inlines(self):
        """veripy_eval should call block functions, not contain block logic."""
        c = emit_c(self._counter_ir())
        # Extract just the veripy_eval body
        start = c.index('void veripy_eval(')
        # Find the matching closing brace
        depth = 0
        for i, ch in enumerate(c[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    eval_body = c[start:i + 1]
                    break
        # eval body should NOT contain the actual assignment logic
        self.assertNotIn('s->cnt =', eval_body)
        # but should contain function calls
        self.assertIn('_seq_0(s)', eval_body)
        self.assertIn('_cont_assigns(s)', eval_body)

    def test_inline_hint_small_block(self):
        """Small blocks get always_inline."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 8), Port('out', 'output', 8)],
            assigns=[ContAssign('out', Sig('a'))])
        c = emit_c(ir)
        self.assertIn('__attribute__((always_inline))', c)

    def test_no_inline_hint_large_block(self):
        """Blocks exceeding threshold do NOT get always_inline."""
        # Create a comb block with > _INLINE_THRESHOLD statements
        stmts = [Assign(f'out', Const(i)) for i in range(_INLINE_THRESHOLD + 1)]
        ir = IRModule(name='t',
            ports=[Port('out', 'output', 8)],
            comb_blocks=[CombBlock(stmts=stmts, locals={})])
        c = emit_c(ir)
        # _comb_0 should NOT have always_inline
        idx = c.index('_comb_0')
        # Get the line containing _comb_0 definition
        line_start = c.rfind('\n', 0, idx) + 1
        line_end = c.index('\n', idx)
        defn_line = c[line_start:line_end]
        self.assertNotIn('always_inline', defn_line)


class TestCountStmts(unittest.TestCase):
    """Test _count_stmts helper."""

    def test_flat(self):
        stmts = [Assign('x', Const(1)), Assign('y', Const(2))]
        self.assertEqual(_count_stmts(stmts), 2)

    def test_nested_if(self):
        stmts = [If(Sig('a'), [Assign('x', Const(1))], [Assign('y', Const(2))])]
        # 1 (If) + 1 (then) + 1 (else) = 3
        self.assertEqual(_count_stmts(stmts), 3)

    def test_empty(self):
        self.assertEqual(_count_stmts([]), 0)


class TestCSimModel(unittest.TestCase):
    """Test compile + run cycle via CSimModel."""

    def _counter_ir(self):
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
                locals={})])

    def test_counter_reset(self):
        with CSimModel(self._counter_ir()) as m:
            m.set('reset', 1)
            m.set('enable', 0)
            m.step('clock')
            m.eval()
            self.assertEqual(m.get('count'), 0)

    def test_counter_counts(self):
        with CSimModel(self._counter_ir()) as m:
            m.set('reset', 1)
            m.set('enable', 1)
            m.step('clock')
            m.set('reset', 0)
            for _ in range(5):
                m.step('clock')
            m.eval()
            self.assertEqual(m.get('count'), 5)

    def test_counter_wraps(self):
        """4-bit counter wraps at 16."""
        with CSimModel(self._counter_ir()) as m:
            m.set('reset', 1)
            m.set('enable', 1)
            m.step('clock')
            m.set('reset', 0)
            for _ in range(17):
                m.step('clock')
            m.eval()
            self.assertEqual(m.get('count'), 1)  # 17 mod 16 = 1

    def test_comb_passthrough(self):
        """Pure combinational: out = a + b."""
        ir = IRModule(name='adder',
            ports=[Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('out', 'output', 8)],
            assigns=[ContAssign('out', BinOp('+', Sig('a'), Sig('b')))])
        with CSimModel(ir) as m:
            m.set('a', 3)
            m.set('b', 7)
            m.eval()
            self.assertEqual(m.get('out'), 10)

    def test_mux(self):
        ir = IRModule(name='mux',
            ports=[Port('sel', 'input', 1), Port('a', 'input', 8),
                   Port('b', 'input', 8), Port('out', 'output', 8)],
            assigns=[ContAssign('out', Mux(Sig('sel'), Sig('a'), Sig('b')))])
        with CSimModel(ir) as m:
            m.set('a', 42)
            m.set('b', 99)
            m.set('sel', 1)
            m.eval()
            self.assertEqual(m.get('out'), 42)
            m.set('sel', 0)
            m.eval()
            self.assertEqual(m.get('out'), 99)

    def test_mem_read_write(self):
        """Write to memory via seq block, read via comb assign."""
        ir = IRModule(name='mem_rw',
            ports=[Port('clock', 'input', 1), Port('wen', 'input', 1),
                   Port('waddr', 'input', 2), Port('wdata', 'input', 8),
                   Port('raddr', 'input', 2), Port('rdata', 'output', 8)],
            mems=[MemDecl('buf', 4, 8)],
            assigns=[ContAssign('rdata', Index(Sig('buf'), Sig('raddr')))],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[If(Sig('wen'),
                          [MemWrite('buf', Sig('waddr'), Sig('wdata'), False)],
                          [])],
                locals={})])
        with CSimModel(ir) as m:
            # Write 0xAB to address 2
            m.set('wen', 1)
            m.set('waddr', 2)
            m.set('wdata', 0xAB)
            m.step('clock')
            # Read back
            m.set('wen', 0)
            m.set('raddr', 2)
            m.eval()
            self.assertEqual(m.get('rdata'), 0xAB)

    def test_instances_rejected(self):
        ir = IRModule(name='bad',
            ports=[Port('x', 'input', 1)],
            instances=[object()])  # non-empty
        with self.assertRaises(ValueError):
            CSimModel(ir)

    def test_concat(self):
        """out = {a[3:0], b[3:0]}"""
        ir = IRModule(name='cat',
            ports=[Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('out', 'output', 8)],
            assigns=[ContAssign('out', Concat([
                Slice(Sig('a'), Const(3), Const(0)),
                Slice(Sig('b'), Const(3), Const(0)),
            ]))])
        with CSimModel(ir) as m:
            m.set('a', 0x5A)  # low nibble = 0xA
            m.set('b', 0x3C)  # low nibble = 0xC
            m.eval()
            # out = {0xA, 0xC} = 0xAC
            self.assertEqual(m.get('out'), 0xAC)

    def test_compare(self):
        ir = IRModule(name='cmp',
            ports=[Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('eq', 'output', 1)],
            assigns=[ContAssign('eq', Compare('==', Sig('a'), Sig('b')))])
        with CSimModel(ir) as m:
            m.set('a', 5)
            m.set('b', 5)
            m.eval()
            self.assertEqual(m.get('eq'), 1)
            m.set('b', 6)
            m.eval()
            self.assertEqual(m.get('eq'), 0)


if __name__ == '__main__':
    unittest.main()
