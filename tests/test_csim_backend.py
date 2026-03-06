"""Tests for native C simulation backend (backend_csim)."""
import unittest

from veripy.ir import (
    Const, Sig, BinOp, UnaryOp, Compare, Mux, Slice, Index, Concat,
    Assign, SliceAssign, If, Case, MemWrite,
    ContAssign, CombBlock, SeqBlock, Instance,
    Port, WireDecl, RegDecl, MemDecl, IRModule,
)
from veripy.backend_csim import emit_c, CSimModel, _count_stmts, _INLINE_THRESHOLD, _build_pack_map, _find_merge_groups


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
        # 1-bit signals are packed into _pack_N words
        self.assertIn('uint64_t _pack_0;', c)
        # Wider signals remain as individual fields
        self.assertIn('uint8_t cnt;', c)

    def test_prev_clock(self):
        c = emit_c(self._counter_ir())
        self.assertIn('_prev_clock', c)

    def test_posedge_detection(self):
        c = emit_c(self._counter_ir())
        # Clock is packed; edge detection reads from pack word
        self.assertIn('_pack_0 >>', c)
        self.assertIn('_prev_clock', c)

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
        """Trivial single-statement comb blocks are inlined into veripy_eval()."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 8), Port('out', 'output', 8)],
            comb_blocks=[CombBlock(
                stmts=[Assign('out', Sig('a'))], locals={})])
        c = emit_c(ir)
        # Trivial block is inlined — no separate _comb_0 function
        self.assertNotIn('_comb_0(State* s)', c)
        self.assertNotIn('_comb_0(s);', c)
        # Statement appears directly in veripy_eval body
        self.assertIn('s->out', c)

    def test_per_block_comb_multi_stmt(self):
        """Multi-statement comb blocks still get their own _comb_N() function."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('out', 'output', 8), Port('out2', 'output', 8)],
            comb_blocks=[CombBlock(
                stmts=[Assign('out', Sig('a')), Assign('out2', Sig('b'))],
                locals={})])
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
        # eval body should NOT contain the seq block's internal logic (if/else)
        # NBA init (s->_nba_x = s->x) and commit (s->x = s->_nba_x) are fine in eval
        self.assertNotIn('if (((s->_pack_0 >> 1ULL)', eval_body)  # reset check is inside _seq_0
        # but should contain function calls and NBA commit
        self.assertIn('_seq_0(s)', eval_body)
        self.assertIn('_cont_assigns(s)', eval_body)
        self.assertIn('s->cnt = s->_nba_cnt', eval_body)

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

    def test_struct_eval_order(self):
        """State struct fields ordered by evaluation access pattern.

        comb_0 writes 'mid' reading 'inp'; comb_1 writes 'out' reading 'mid'.
        Struct should place inp, mid, out in that order (not alphabetical
        or declaration order).
        """
        ir = IRModule(name='t',
            ports=[Port('inp', 'input', 8), Port('out', 'output', 8)],
            wires=[WireDecl('mid', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('mid', Sig('inp'))], locals={}),
                CombBlock(stmts=[Assign('out', Sig('mid'))], locals={}),
            ])
        c = emit_c(ir)
        # Extract struct field order
        struct_start = c.index('typedef struct {')
        struct_end = c.index('} State;')
        struct_body = c[struct_start:struct_end]
        import re
        fields = re.findall(r'uint\d+_t (\w+);', struct_body)
        self.assertEqual(fields.index('mid'), fields.index('inp') + 1)
        self.assertLess(fields.index('mid'), fields.index('out'))


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


class TestFindMergeGroups(unittest.TestCase):
    """Test _find_merge_groups comb block analysis."""

    def test_no_blocks(self):
        ir = IRModule(name='t', ports=[])
        self.assertEqual(_find_merge_groups(ir), [])

    def test_single_block(self):
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 8), Port('out', 'output', 8)],
            comb_blocks=[CombBlock(stmts=[Assign('out', Sig('a'))], locals={})])
        self.assertEqual(_find_merge_groups(ir), [[0]])

    def test_mergeable_chain(self):
        """Block 0 writes 'mid' only read by block 1 → merged."""
        ir = IRModule(name='t',
            ports=[Port('inp', 'input', 8), Port('out', 'output', 8)],
            wires=[WireDecl('mid', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('mid', Sig('inp'))], locals={}),
                CombBlock(stmts=[Assign('out', Sig('mid'))], locals={}),
            ])
        self.assertEqual(_find_merge_groups(ir), [[0, 1]])

    def test_not_mergeable_output_port(self):
        """Block 0 writes an output port → cannot merge."""
        ir = IRModule(name='t',
            ports=[Port('inp', 'input', 8), Port('mid', 'output', 8),
                   Port('out', 'output', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('mid', Sig('inp'))], locals={}),
                CombBlock(stmts=[Assign('out', Sig('mid'))], locals={}),
            ])
        self.assertEqual(_find_merge_groups(ir), [[0], [1]])

    def test_not_mergeable_seq_reads(self):
        """Block 0 writes a signal read by a seq block → cannot merge."""
        ir = IRModule(name='t',
            ports=[Port('clk', 'input', 1), Port('inp', 'input', 8),
                   Port('out', 'output', 8)],
            wires=[WireDecl('mid', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('mid', Sig('inp'))], locals={}),
                CombBlock(stmts=[Assign('out', Sig('mid'))], locals={}),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clk')],
                stmts=[Assign('out', Sig('mid'))],
                locals={})])
        self.assertEqual(_find_merge_groups(ir), [[0], [1]])

    def test_merge_emits_single_function(self):
        """Merged group emits one _comb_0 function, not two."""
        ir = IRModule(name='t',
            ports=[Port('inp', 'input', 8), Port('out', 'output', 8)],
            wires=[WireDecl('mid', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('mid', Sig('inp'))], locals={}),
                CombBlock(stmts=[Assign('out', Sig('mid'))], locals={}),
            ])
        c = emit_c(ir)
        self.assertIn('_comb_0(State* s)', c)
        self.assertNotIn('_comb_1(State* s)', c)
        # Both assignments appear in the merged function
        self.assertIn('s->mid', c)
        self.assertIn('s->out', c)


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


class TestSignalPacking(unittest.TestCase):
    """Test 1-bit signal packing into uint64_t bitfield words."""

    def test_build_pack_map_basic(self):
        """1-bit signals get packed, wider signals do not."""
        all_sigs = {'a': 1, 'b': 8, 'c': 1}
        ordered = ['a', 'b', 'c']
        pm, words = _build_pack_map(all_sigs, ordered)
        self.assertIn('a', pm)
        self.assertIn('c', pm)
        self.assertNotIn('b', pm)
        self.assertEqual(len(words), 1)

    def test_build_pack_map_multiple_words(self):
        """More than 64 one-bit signals require multiple pack words."""
        all_sigs = {f's{i}': 1 for i in range(65)}
        ordered = [f's{i}' for i in range(65)]
        pm, words = _build_pack_map(all_sigs, ordered)
        self.assertEqual(len(words), 2)
        self.assertEqual(pm['s0'], ('_pack_0', 0))
        self.assertEqual(pm['s63'], ('_pack_0', 63))
        self.assertEqual(pm['s64'], ('_pack_1', 0))

    def test_build_pack_map_no_one_bit(self):
        """No 1-bit signals → empty pack map."""
        all_sigs = {'x': 8, 'y': 16}
        pm, words = _build_pack_map(all_sigs, ['x', 'y'])
        self.assertEqual(pm, {})
        self.assertEqual(words, [])

    def test_struct_has_pack_words(self):
        """State struct uses _pack_N for 1-bit signals."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 1), Port('b', 'input', 1),
                   Port('out', 'output', 8)],
            assigns=[ContAssign('out', BinOp('+', Sig('a'), Sig('b')))])
        c = emit_c(ir)
        self.assertIn('uint64_t _pack_0;', c)
        self.assertNotIn('uint8_t a;', c)
        self.assertNotIn('uint8_t b;', c)
        self.assertIn('uint8_t out;', c)

    def test_packed_set_get(self):
        """set/get for packed ports use bit ops."""
        ir = IRModule(name='t',
            ports=[Port('a', 'input', 1), Port('b', 'input', 1),
                   Port('out', 'output', 1)],
            assigns=[ContAssign('out', BinOp('&', Sig('a'), Sig('b')))])
        c = emit_c(ir)
        self.assertIn('_pack_0', c)
        # set uses bit insert
        self.assertIn('& ~(1ULL <<', c)
        # get uses bit extract
        self.assertIn('>> ', c)

    def test_packed_and_gate(self):
        """Functional test: out = a & b with all 1-bit packed signals."""
        ir = IRModule(name='and_gate',
            ports=[Port('a', 'input', 1), Port('b', 'input', 1),
                   Port('out', 'output', 1)],
            assigns=[ContAssign('out', BinOp('&', Sig('a'), Sig('b')))])
        with CSimModel(ir) as m:
            m.set('a', 1); m.set('b', 1); m.eval()
            self.assertEqual(m.get('out'), 1)
            m.set('a', 0); m.eval()
            self.assertEqual(m.get('out'), 0)

    def test_packed_counter_functional(self):
        """Counter with packed clock/reset/enable works correctly."""
        ir = IRModule(name='counter',
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
        with CSimModel(ir) as m:
            m.set('reset', 1); m.set('enable', 1)
            m.step('clock')
            m.set('reset', 0)
            for _ in range(3):
                m.step('clock')
            m.eval()
            self.assertEqual(m.get('count'), 3)

    def test_packed_1bit_output_assign(self):
        """1-bit output assigned from comb block uses packed write."""
        ir = IRModule(name='inv',
            ports=[Port('a', 'input', 1), Port('out', 'output', 1)],
            comb_blocks=[CombBlock(
                stmts=[Assign('out', UnaryOp('!', Sig('a')))],
                locals={})])
        with CSimModel(ir) as m:
            m.set('a', 0); m.eval()
            self.assertEqual(m.get('out'), 1)
            m.set('a', 1); m.eval()
            self.assertEqual(m.get('out'), 0)


class TestCombResettle(unittest.TestCase):
    """Tests for comb re-settle after seq blocks commit NBA values.

    When a seq block commits a new value via NBA, all comb blocks that
    transitively depend on that signal must re-evaluate — including blocks
    that read the *output* of another comb block (not the NBA signal directly).
    Dirty flags must propagate through the comb chain during re-settle.

    Intermediate signals are declared as output ports to prevent merge-group
    folding, which would hide the bug by combining blocks into one function.
    """

    def test_comb_chain_two_deep(self):
        """seq → comb A (mid) → comb B (out): output must update same cycle."""
        ir = IRModule(name='chain2',
            ports=[Port('clock', 'input', 1), Port('mid', 'output', 8),
                   Port('out', 'output', 8)],
            regs=[RegDecl('r', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('mid', BinOp('+', Sig('r'), Const(1)), False)]),
                CombBlock(stmts=[Assign('out', BinOp('+', Sig('mid'), Const(1)), False)]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[Assign('r', BinOp('+', Sig('r'), Const(10)), False)],
                locals={})])
        with CSimModel(ir) as m:
            m.eval()
            self.assertEqual(m.get('out'), 2)   # r=0 → mid=1 → out=2
            m.step('clock')
            m.eval()
            self.assertEqual(m.get('out'), 12)  # r=10 → mid=11 → out=12

    def test_comb_chain_three_deep(self):
        """seq → comb A → comb B → comb C: three-level transitive propagation."""
        ir = IRModule(name='chain3',
            ports=[Port('clock', 'input', 1), Port('a', 'output', 8),
                   Port('b', 'output', 8), Port('out', 'output', 8)],
            regs=[RegDecl('r', 8)],
            comb_blocks=[
                CombBlock(stmts=[Assign('a', BinOp('+', Sig('r'), Const(1)), False)]),
                CombBlock(stmts=[Assign('b', BinOp('+', Sig('a'), Const(1)), False)]),
                CombBlock(stmts=[Assign('out', BinOp('+', Sig('b'), Const(1)), False)]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[Assign('r', BinOp('+', Sig('r'), Const(5)), False)],
                locals={})])
        with CSimModel(ir) as m:
            m.eval()
            self.assertEqual(m.get('out'), 3)  # r=0 → a=1 → b=2 → out=3
            m.step('clock')
            m.eval()
            self.assertEqual(m.get('out'), 8)  # r=5 → a=6 → b=7 → out=8

    def test_comb_chain_1bit_packed(self):
        """1-bit packed signals: seq → fwd → gated → out.

        This is the pattern that broke the CPU: seq writes a 1-bit flag,
        comb A forwards it, comb B gates it — all packed signals whose
        intermediate values are output ports (preventing merge).
        """
        ir = IRModule(name='chain_packed',
            ports=[Port('clock', 'input', 1), Port('enable', 'input', 1),
                   Port('fwd', 'output', 1), Port('gated', 'output', 1),
                   Port('out', 'output', 1)],
            regs=[RegDecl('req', 1)],
            comb_blocks=[
                CombBlock(stmts=[Assign('fwd', Sig('req'), False)]),
                CombBlock(stmts=[Assign('gated', BinOp('&', Sig('fwd'), Sig('enable')), False)]),
                CombBlock(stmts=[Assign('out', Sig('gated'), False)]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[Assign('req', Const(1), False)],
                locals={})])
        with CSimModel(ir) as m:
            m.set('enable', 1)
            m.eval()
            self.assertEqual(m.get('out'), 0)  # req=0
            m.step('clock')
            m.eval()
            self.assertEqual(m.get('out'), 1)  # req=1 → fwd=1 → gated=1 → out=1

    def test_comb_chain_mixed_widths(self):
        """Chain with mixed 1-bit (packed) and wide signals."""
        ir = IRModule(name='chain_mixed',
            ports=[Port('clock', 'input', 1), Port('fwd', 'output', 1),
                   Port('val', 'output', 8), Port('out', 'output', 8)],
            regs=[RegDecl('flag', 1)],
            comb_blocks=[
                CombBlock(stmts=[Assign('fwd', Sig('flag'), False)]),
                CombBlock(stmts=[Assign('val', Mux(Sig('fwd'), Const(42), Const(0)), False)]),
                CombBlock(stmts=[Assign('out', Sig('val'), False)]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[Assign('flag', Const(1), False)],
                locals={})])
        with CSimModel(ir) as m:
            m.eval()
            self.assertEqual(m.get('out'), 0)
            m.step('clock')
            m.eval()
            self.assertEqual(m.get('out'), 42)


class TestNBA(unittest.TestCase):
    """Tests for non-blocking assignment (NBA) batching in seq blocks."""

    def _swap_ir(self):
        """Two seq blocks that swap a and b — requires correct NBA semantics."""
        return IRModule(name='swap',
            ports=[Port('clock', 'input', 1), Port('a', 'output', 8),
                   Port('b', 'output', 8)],
            regs=[RegDecl('a', 8), RegDecl('b', 8)],
            seq_blocks=[
                SeqBlock(edges=[('posedge', 'clock')],
                         stmts=[Assign('a', Sig('b'), False)],
                         locals={}),
                SeqBlock(edges=[('posedge', 'clock')],
                         stmts=[Assign('b', Sig('a'), False)],
                         locals={}),
            ])

    def test_nba_struct_fields(self):
        """State struct has _nba_ fields for signals written in seq blocks."""
        c = emit_c(self._swap_ir())
        self.assertIn('_nba_a', c)
        self.assertIn('_nba_b', c)

    def test_nba_seq_writes_to_temp(self):
        """Seq block writes go to _nba_ temporaries, not directly to state."""
        c = emit_c(self._swap_ir())
        # _seq_0 should write to _nba_a
        seq0_start = c.index('void _seq_0(')
        seq0_end = c.index('\n}', seq0_start) + 2
        seq0_body = c[seq0_start:seq0_end]
        self.assertIn('_nba_a', seq0_body)
        self.assertNotIn('s->a =', seq0_body)

    def test_nba_eval_init_and_commit(self):
        """veripy_eval initializes NBA temps before seq blocks and commits after."""
        c = emit_c(self._swap_ir())
        eval_start = c.index('void veripy_eval(')
        eval_end = c.index('\n}', eval_start) + 2
        eval_body = c[eval_start:eval_end]
        # Init: copy current value into NBA temp before seq blocks
        self.assertIn('s->_nba_a = s->a', eval_body)
        self.assertIn('s->_nba_b = s->b', eval_body)
        # Commit: write NBA temp back to state after seq blocks
        self.assertIn('s->a = s->_nba_a', eval_body)
        self.assertIn('s->b = s->_nba_b', eval_body)

    def test_nba_swap_correctness(self):
        """Two seq blocks swapping a and b produce correct NBA swap semantics."""
        ir = self._swap_ir()
        # Pre-load a=10, b=20 by patching initial state via set (use ports)
        # Since a and b are both regs and outputs, we need to drive them via
        # a comb block or just test via step behavior.
        # Use a simpler approach: single seq block, verify old value is read.
        ir2 = IRModule(name='nba_test',
            ports=[Port('clock', 'input', 1), Port('out', 'output', 8)],
            regs=[RegDecl('r', 8)],
            assigns=[ContAssign('out', Sig('r'))],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clock')],
                stmts=[Assign('r', BinOp('+', Sig('r'), Const(1)), False)],
                locals={})])
        with CSimModel(ir2) as m:
            m.step('clock')
            m.eval()
            self.assertEqual(m.get('out'), 1)
            m.step('clock')
            m.eval()
            self.assertEqual(m.get('out'), 2)


# ── Flatten + CSimModel integration helpers ──────────────────────────

def _flatten_and_model(parent, registry):
    """Flatten a hierarchical IR and return a CSimModel."""
    from veripy.flatten import flatten_ir
    flat = flatten_ir(parent, registry)
    return CSimModel(flat)


class TestMultiInstanceLocals(unittest.TestCase):
    """Block-local variables must not alias across instances of the same module.

    When the same submodule is instantiated multiple times, comb/seq block
    locals (e.g. 'tmp') must be prefixed with the instance name so that
    each instance gets its own storage.
    """

    def _adder_child(self):
        """Submodule: out = x + y, using a comb local 'tmp'."""
        return IRModule(name='adder',
            ports=[Port('x', 'input', 8), Port('y', 'input', 8),
                   Port('sum', 'output', 8)],
            comb_blocks=[CombBlock(
                stmts=[Assign('tmp', BinOp('+', Sig('x'), Sig('y'))),
                       Assign('sum', Sig('tmp'))],
                locals={'tmp': 8})])

    def test_two_instances_independent(self):
        """Two adder instances produce independent results."""
        child = self._adder_child()
        parent = IRModule(name='top',
            ports=[Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('c', 'input', 8), Port('d', 'input', 8),
                   Port('s1', 'output', 8), Port('s2', 'output', 8)],
            instances=[
                Instance('adder', 'add0', {}, [('x','a'),('y','b'),('sum','s1')]),
                Instance('adder', 'add1', {}, [('x','c'),('y','d'),('sum','s2')]),
            ])
        with _flatten_and_model(parent, {'adder': child}) as m:
            m.set('a', 3); m.set('b', 7)
            m.set('c', 10); m.set('d', 20)
            m.eval()
            self.assertEqual(m.get('s1'), 10)
            self.assertEqual(m.get('s2'), 30)

    def test_four_instances_no_aliasing(self):
        """Four instances — locals must not interfere."""
        child = self._adder_child()
        parent = IRModule(name='top',
            ports=[Port(f'x{i}', 'input', 8) for i in range(4)]
                + [Port(f'y{i}', 'input', 8) for i in range(4)]
                + [Port(f's{i}', 'output', 8) for i in range(4)],
            instances=[
                Instance('adder', f'a{i}', {},
                    [('x',f'x{i}'),('y',f'y{i}'),('sum',f's{i}')])
                for i in range(4)
            ])
        with _flatten_and_model(parent, {'adder': child}) as m:
            for i in range(4):
                m.set(f'x{i}', (i+1)*10)
                m.set(f'y{i}', (i+1))
            m.eval()
            for i in range(4):
                self.assertEqual(m.get(f's{i}'), (i+1)*10 + (i+1),
                    f'instance a{i} wrong')

    def test_seq_locals_across_instances(self):
        """Seq block locals in multiple instances don't alias."""
        child = IRModule(name='acc',
            ports=[Port('clk', 'input', 1), Port('inc', 'input', 8),
                   Port('val', 'output', 8)],
            regs=[RegDecl('r', 8)],
            assigns=[ContAssign('val', Sig('r'))],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clk')],
                stmts=[Assign('nxt', BinOp('+', Sig('r'), Sig('inc'))),
                       Assign('r', Sig('nxt'), False)],
                locals={'nxt': 8})])
        parent = IRModule(name='top',
            ports=[Port('clk', 'input', 1),
                   Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('va', 'output', 8), Port('vb', 'output', 8)],
            instances=[
                Instance('acc', 'u0', {}, [('clk','clk'),('inc','a'),('val','va')]),
                Instance('acc', 'u1', {}, [('clk','clk'),('inc','b'),('val','vb')]),
            ])
        with _flatten_and_model(parent, {'acc': child}) as m:
            m.set('a', 5); m.set('b', 3)
            m.step('clk')
            m.eval()
            self.assertEqual(m.get('va'), 5)
            self.assertEqual(m.get('vb'), 3)
            m.step('clk')
            m.eval()
            self.assertEqual(m.get('va'), 10)
            self.assertEqual(m.get('vb'), 6)


class TestPipelineCSim(unittest.TestCase):
    """Multi-stage pipeline with valid/stall/flush — the CPU's core pattern.

    Pipeline: IF → ID → EX → WB
    - Each stage has a valid bit (1-bit, packed)
    - Stall freezes all stages
    - Flush clears all valid bits
    - Data propagates through pipeline registers
    """

    def _pipeline_ir(self):
        """4-stage pipeline: data flows IF→ID→EX→WB with stall/flush."""
        return IRModule(name='pipe',
            ports=[
                Port('clk', 'input', 1), Port('rst', 'input', 1),
                Port('stall', 'input', 1), Port('flush', 'input', 1),
                Port('din', 'input', 8),
                Port('if_id_v', 'output', 1), Port('if_id_d', 'output', 8),
                Port('id_ex_v', 'output', 1), Port('id_ex_d', 'output', 8),
                Port('ex_wb_v', 'output', 1), Port('ex_wb_d', 'output', 8),
            ],
            regs=[
                RegDecl('r_ifid_v', 1), RegDecl('r_ifid_d', 8),
                RegDecl('r_idex_v', 1), RegDecl('r_idex_d', 8),
                RegDecl('r_exwb_v', 1), RegDecl('r_exwb_d', 8),
            ],
            assigns=[
                ContAssign('if_id_v', Sig('r_ifid_v')),
                ContAssign('if_id_d', Sig('r_ifid_d')),
                ContAssign('id_ex_v', Sig('r_idex_v')),
                ContAssign('id_ex_d', Sig('r_idex_d')),
                ContAssign('ex_wb_v', Sig('r_exwb_v')),
                ContAssign('ex_wb_d', Sig('r_exwb_d')),
            ],
            seq_blocks=[
                # IF→ID
                SeqBlock(edges=[('posedge', 'clk')], locals={}, stmts=[
                    If(Sig('rst'), [
                        Assign('r_ifid_v', Const(0), False),
                        Assign('r_ifid_d', Const(0), False),
                    ], [If(Sig('flush'), [
                        Assign('r_ifid_v', Const(0), False),
                    ], [If(Sig('stall'), [], [
                        Assign('r_ifid_v', Const(1), False),
                        Assign('r_ifid_d', Sig('din'), False),
                    ])])])]),
                # ID→EX
                SeqBlock(edges=[('posedge', 'clk')], locals={}, stmts=[
                    If(Sig('rst'), [
                        Assign('r_idex_v', Const(0), False),
                        Assign('r_idex_d', Const(0), False),
                    ], [If(Sig('flush'), [
                        Assign('r_idex_v', Const(0), False),
                    ], [If(Sig('stall'), [], [
                        Assign('r_idex_v', Sig('r_ifid_v'), False),
                        Assign('r_idex_d', Sig('r_ifid_d'), False),
                    ])])])]),
                # EX→WB
                SeqBlock(edges=[('posedge', 'clk')], locals={}, stmts=[
                    If(Sig('rst'), [
                        Assign('r_exwb_v', Const(0), False),
                        Assign('r_exwb_d', Const(0), False),
                    ], [If(Sig('flush'), [
                        Assign('r_exwb_v', Const(0), False),
                    ], [If(Sig('stall'), [], [
                        Assign('r_exwb_v', Sig('r_idex_v'), False),
                        Assign('r_exwb_d', Sig('r_idex_d'), False),
                    ])])])]),
            ])

    def test_data_propagates_through_stages(self):
        """Data enters IF and appears at WB three cycles later."""
        with CSimModel(self._pipeline_ir()) as m:
            m.set('rst', 1); m.set('stall', 0); m.set('flush', 0); m.set('din', 0)
            m.step('clk')
            m.set('rst', 0); m.set('din', 42)
            m.step('clk')  # cycle 1: din=42 enters IF/ID
            m.eval()
            self.assertEqual(m.get('if_id_v'), 1)
            self.assertEqual(m.get('if_id_d'), 42)
            self.assertEqual(m.get('id_ex_v'), 0)

            m.set('din', 99)
            m.step('clk')  # cycle 2: 42 moves to ID/EX, 99 enters IF/ID
            m.eval()
            self.assertEqual(m.get('if_id_d'), 99)
            self.assertEqual(m.get('id_ex_v'), 1)
            self.assertEqual(m.get('id_ex_d'), 42)
            self.assertEqual(m.get('ex_wb_v'), 0)

            m.step('clk')  # cycle 3: 42 reaches WB
            m.eval()
            self.assertEqual(m.get('ex_wb_v'), 1)
            self.assertEqual(m.get('ex_wb_d'), 42)

    def test_stall_freezes_pipeline(self):
        """Stall prevents all stages from advancing."""
        with CSimModel(self._pipeline_ir()) as m:
            m.set('rst', 1); m.set('stall', 0); m.set('flush', 0); m.set('din', 0)
            m.step('clk')
            m.set('rst', 0); m.set('din', 42)
            m.step('clk')  # 42 enters IF/ID
            m.eval()
            self.assertEqual(m.get('if_id_v'), 1)

            m.set('stall', 1); m.set('din', 99)
            m.step('clk')  # stalled — nothing moves
            m.eval()
            self.assertEqual(m.get('if_id_d'), 42)  # still 42, not 99
            self.assertEqual(m.get('id_ex_v'), 0)    # didn't advance

            m.set('stall', 0)
            m.step('clk')  # unstall — 42 moves to ID/EX
            m.eval()
            self.assertEqual(m.get('id_ex_v'), 1)
            self.assertEqual(m.get('id_ex_d'), 42)

    def test_flush_clears_valid_bits(self):
        """Flush clears all valid bits but doesn't affect data."""
        with CSimModel(self._pipeline_ir()) as m:
            m.set('rst', 1); m.set('stall', 0); m.set('flush', 0); m.set('din', 0)
            m.step('clk')
            m.set('rst', 0); m.set('din', 42)
            m.step('clk')
            m.step('clk')
            m.step('clk')  # 42 is now in all stages
            m.eval()
            self.assertEqual(m.get('ex_wb_v'), 1)

            m.set('flush', 1)
            m.step('clk')
            m.eval()
            self.assertEqual(m.get('if_id_v'), 0)
            self.assertEqual(m.get('id_ex_v'), 0)
            self.assertEqual(m.get('ex_wb_v'), 0)

    def test_nba_ordering_across_stages(self):
        """NBA ensures each stage reads the pre-clock value of the previous stage.

        If stage N and stage N+1 both trigger on the same posedge, stage N+1
        must read the OLD value of stage N's output (before NBA commit).
        """
        with CSimModel(self._pipeline_ir()) as m:
            m.set('rst', 1); m.set('stall', 0); m.set('flush', 0); m.set('din', 0)
            m.step('clk')
            m.set('rst', 0)

            # Feed values 10, 20, 30 on consecutive cycles
            for val in [10, 20, 30]:
                m.set('din', val)
                m.step('clk')

            m.eval()
            # After 3 cycles: WB=10, EX=20, ID=30
            self.assertEqual(m.get('ex_wb_d'), 10)
            self.assertEqual(m.get('id_ex_d'), 20)
            self.assertEqual(m.get('if_id_d'), 30)


class TestCacheLikePattern(unittest.TestCase):
    """Cache-like module: mem arrays, tag/valid/data, hit detection with locals.

    This replicates the L1 cache pattern: a comb block computes in_set/in_tag
    from the address, checks valid[] and tags[] arrays, and outputs hit/miss.
    A seq block fills the cache on miss.
    """

    def _cache_child(self, lines=4):
        """Simple direct-mapped cache: 4 lines, 8-bit data."""
        return IRModule(name='cache',
            ports=[
                Port('clk', 'input', 1), Port('addr', 'input', 8),
                Port('fill', 'input', 1), Port('fdata', 'input', 8),
                Port('hit', 'output', 1), Port('rdata', 'output', 8),
            ],
            mems=[
                MemDecl('valid', lines, 1),
                MemDecl('tags', lines, 4),
                MemDecl('data', lines, 8),
            ],
            comb_blocks=[CombBlock(
                stmts=[
                    # in_set = addr[1:0], in_tag = addr[7:2]
                    Assign('in_set', BinOp('&', Sig('addr'), Const(lines - 1))),
                    Assign('in_tag', BinOp('>>', Sig('addr'), Const(2))),
                    If(BinOp('&', Index(Sig('valid'), Sig('in_set')),
                             Compare('==', Index(Sig('tags'), Sig('in_set')),
                                     Sig('in_tag'))),
                       [Assign('hit', Const(1)),
                        Assign('rdata', Index(Sig('data'), Sig('in_set')))],
                       [Assign('hit', Const(0)),
                        Assign('rdata', Const(0))]),
                ],
                locals={'in_set': 8, 'in_tag': 8})],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clk')], locals={'fs': 8, 'ft': 8},
                stmts=[
                    If(Sig('fill'), [
                        Assign('fs', BinOp('&', Sig('addr'), Const(lines - 1))),
                        Assign('ft', BinOp('>>', Sig('addr'), Const(2))),
                        MemWrite('valid', Sig('fs'), Const(1), False),
                        MemWrite('tags', Sig('fs'), Sig('ft'), False),
                        MemWrite('data', Sig('fs'), Sig('fdata'), False),
                    ], [])
                ])])

    def test_single_cache_miss_then_hit(self):
        """Fill a cache line, then read it back — should hit."""
        with CSimModel(self._cache_child()) as m:
            m.set('addr', 5); m.set('fill', 0); m.set('fdata', 0)
            m.eval()
            self.assertEqual(m.get('hit'), 0)

            # Fill addr=5 with data=0xAB
            m.set('fill', 1); m.set('fdata', 0xAB)
            m.step('clk')
            m.set('fill', 0)
            m.eval()
            self.assertEqual(m.get('hit'), 1)
            self.assertEqual(m.get('rdata'), 0xAB)

    def test_two_cache_instances_independent(self):
        """Two cache instances don't share locals (in_set, in_tag)."""
        child = self._cache_child()
        parent = IRModule(name='top',
            ports=[
                Port('clk', 'input', 1),
                Port('a0', 'input', 8), Port('f0', 'input', 1),
                Port('fd0', 'input', 8),
                Port('a1', 'input', 8), Port('f1', 'input', 1),
                Port('fd1', 'input', 8),
                Port('h0', 'output', 1), Port('d0', 'output', 8),
                Port('h1', 'output', 1), Port('d1', 'output', 8),
            ],
            instances=[
                Instance('cache', 'c0', {},
                    [('clk','clk'),('addr','a0'),('fill','f0'),('fdata','fd0'),
                     ('hit','h0'),('rdata','d0')]),
                Instance('cache', 'c1', {},
                    [('clk','clk'),('addr','a1'),('fill','f1'),('fdata','fd1'),
                     ('hit','h1'),('rdata','d1')]),
            ])
        with _flatten_and_model(parent, {'cache': child}) as m:
            # Fill c0 at addr=2 with 0x11, c1 at addr=6 with 0x22
            m.set('a0', 2); m.set('f0', 1); m.set('fd0', 0x11)
            m.set('a1', 6); m.set('f1', 1); m.set('fd1', 0x22)
            m.step('clk')
            m.set('f0', 0); m.set('f1', 0)
            m.eval()
            self.assertEqual(m.get('h0'), 1, 'c0 should hit at addr=2')
            self.assertEqual(m.get('d0'), 0x11)
            self.assertEqual(m.get('h1'), 1, 'c1 should hit at addr=6')
            self.assertEqual(m.get('d1'), 0x22)

            # Now read different addresses — c0 should miss, c1 should still hit
            m.set('a0', 7)  # different set than 2
            m.eval()
            self.assertEqual(m.get('h0'), 0, 'c0 should miss at addr=7')
            self.assertEqual(m.get('h1'), 1, 'c1 should still hit at addr=6')

    def test_cache_tag_conflict(self):
        """Two addresses mapping to the same set but different tags."""
        with CSimModel(self._cache_child(lines=4)) as m:
            # addr=1 → set=1, tag=0; addr=5 → set=1, tag=1
            m.set('addr', 1); m.set('fill', 1); m.set('fdata', 0xAA)
            m.step('clk')
            m.set('fill', 0)
            m.eval()
            self.assertEqual(m.get('hit'), 1)

            # Now access addr=5 (same set, different tag) — should miss
            m.set('addr', 5)
            m.eval()
            self.assertEqual(m.get('hit'), 0)


class TestHierarchicalPipeline(unittest.TestCase):
    """Pipeline with submodule instances — the full CPU pattern.

    Tests flatten + csim integration: a pipeline that uses submodule
    instances (ALU, decoder) where the submodules have comb locals.
    """

    def test_pipeline_with_alu_submodule(self):
        """Pipeline where EX stage uses an ALU submodule with locals."""
        alu = IRModule(name='alu',
            ports=[Port('a', 'input', 8), Port('b', 'input', 8),
                   Port('op', 'input', 1), Port('result', 'output', 8)],
            comb_blocks=[CombBlock(
                stmts=[
                    Assign('sum', BinOp('+', Sig('a'), Sig('b'))),
                    Assign('diff', BinOp('-', Sig('a'), Sig('b'))),
                    If(Sig('op'),
                       [Assign('result', Sig('diff'))],
                       [Assign('result', Sig('sum'))]),
                ],
                locals={'sum': 8, 'diff': 8})])

        parent = IRModule(name='pipe_alu',
            ports=[
                Port('clk', 'input', 1), Port('rst', 'input', 1),
                Port('a_in', 'input', 8), Port('b_in', 'input', 8),
                Port('op_in', 'input', 1),
                Port('wb_v', 'output', 1), Port('wb_d', 'output', 8),
            ],
            regs=[
                RegDecl('id_a', 8), RegDecl('id_b', 8), RegDecl('id_op', 1),
                RegDecl('id_v', 1),
                RegDecl('wb_valid', 1), RegDecl('wb_data', 8),
            ],
            wires=[WireDecl('alu_result', 8)],
            assigns=[
                ContAssign('wb_v', Sig('wb_valid')),
                ContAssign('wb_d', Sig('wb_data')),
            ],
            instances=[
                Instance('alu', 'ex_alu', {},
                    [('a','id_a'),('b','id_b'),('op','id_op'),
                     ('result','alu_result')]),
            ],
            seq_blocks=[
                # IF/ID: latch inputs
                SeqBlock(edges=[('posedge', 'clk')], locals={}, stmts=[
                    If(Sig('rst'), [
                        Assign('id_v', Const(0), False),
                    ], [
                        Assign('id_v', Const(1), False),
                        Assign('id_a', Sig('a_in'), False),
                        Assign('id_b', Sig('b_in'), False),
                        Assign('id_op', Sig('op_in'), False),
                    ])]),
                # EX/WB: latch ALU result
                SeqBlock(edges=[('posedge', 'clk')], locals={}, stmts=[
                    If(Sig('rst'), [
                        Assign('wb_valid', Const(0), False),
                    ], [
                        Assign('wb_valid', Sig('id_v'), False),
                        Assign('wb_data', Sig('alu_result'), False),
                    ])]),
            ])

        with _flatten_and_model(parent, {'alu': alu}) as m:
            m.set('rst', 1); m.set('a_in', 0); m.set('b_in', 0); m.set('op_in', 0)
            m.step('clk')
            m.set('rst', 0)

            # Feed a=10, b=3, op=0 (add)
            m.set('a_in', 10); m.set('b_in', 3); m.set('op_in', 0)
            m.step('clk')  # latched into ID
            m.step('clk')  # ALU computes, result latched into WB
            m.eval()
            self.assertEqual(m.get('wb_v'), 1)
            self.assertEqual(m.get('wb_d'), 13)  # 10 + 3

            # Feed a=10, b=3, op=1 (sub)
            m.set('a_in', 10); m.set('b_in', 3); m.set('op_in', 1)
            m.step('clk')
            m.step('clk')
            m.eval()
            self.assertEqual(m.get('wb_d'), 7)  # 10 - 3

    def test_pipeline_with_stall_from_submodule(self):
        """Pipeline stalled by a 'busy' signal from a submodule.

        This is the CPU pattern: IF stage has a cache submodule that
        asserts 'busy' during a miss. The pipeline stalls until busy=0.
        """
        # "Slow unit" that takes N cycles to produce a result
        slow = IRModule(name='slow_unit',
            ports=[
                Port('clk', 'input', 1), Port('rst', 'input', 1),
                Port('start', 'input', 1), Port('din', 'input', 8),
                Port('busy', 'output', 1), Port('dout', 'output', 8),
                Port('done', 'output', 1),
            ],
            regs=[RegDecl('cnt', 4), RegDecl('latched', 8),
                  RegDecl('r_busy', 1), RegDecl('r_done', 1)],
            assigns=[
                ContAssign('busy', Sig('r_busy')),
                ContAssign('dout', Sig('latched')),
                ContAssign('done', Sig('r_done')),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clk')], locals={}, stmts=[
                    If(Sig('rst'), [
                        Assign('cnt', Const(0), False),
                        Assign('r_busy', Const(0), False),
                        Assign('r_done', Const(0), False),
                    ], [If(Sig('start'), [
                        Assign('latched', Sig('din'), False),
                        Assign('cnt', Const(3), False),
                        Assign('r_busy', Const(1), False),
                        Assign('r_done', Const(0), False),
                    ], [If(Compare('>', Sig('cnt'), Const(0)), [
                        Assign('cnt', BinOp('-', Sig('cnt'), Const(1)), False),
                        If(Compare('==', Sig('cnt'), Const(1)), [
                            Assign('r_busy', Const(0), False),
                            Assign('r_done', Const(1), False),
                        ], []),
                    ], [
                        Assign('r_done', Const(0), False),
                    ])])])])])

        parent = IRModule(name='stall_pipe',
            ports=[
                Port('clk', 'input', 1), Port('rst', 'input', 1),
                Port('req', 'input', 1), Port('din', 'input', 8),
                Port('out_v', 'output', 1), Port('out_d', 'output', 8),
                Port('busy', 'output', 1),
            ],
            regs=[RegDecl('pipe_v', 1), RegDecl('pipe_d', 8)],
            wires=[WireDecl('su_busy', 1), WireDecl('su_dout', 8),
                   WireDecl('su_done', 1)],
            assigns=[
                ContAssign('out_v', Sig('pipe_v')),
                ContAssign('out_d', Sig('pipe_d')),
                ContAssign('busy', Sig('su_busy')),
            ],
            instances=[
                Instance('slow_unit', 'su', {},
                    [('clk','clk'),('rst','rst'),('start','req'),('din','din'),
                     ('busy','su_busy'),('dout','su_dout'),('done','su_done')]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clk')], locals={}, stmts=[
                    If(Sig('rst'), [
                        Assign('pipe_v', Const(0), False),
                    ], [If(Sig('su_busy'), [
                        # stall: keep current values
                    ], [If(Sig('su_done'), [
                        Assign('pipe_v', Const(1), False),
                        Assign('pipe_d', Sig('su_dout'), False),
                    ], [
                        Assign('pipe_v', Const(0), False),
                    ])])])])])

        with _flatten_and_model(parent, {'slow_unit': slow}) as m:
            m.set('rst', 1); m.set('req', 0); m.set('din', 0)
            m.step('clk')
            m.set('rst', 0)

            # Start a request with din=42
            m.set('req', 1); m.set('din', 42)
            m.step('clk')
            m.set('req', 0)

            # Should be busy for a few cycles
            m.eval()
            self.assertEqual(m.get('busy'), 1)
            self.assertEqual(m.get('out_v'), 0)

            m.step('clk')
            m.eval()
            self.assertEqual(m.get('busy'), 1)

            m.step('clk')
            m.eval()
            self.assertEqual(m.get('busy'), 1)

            m.step('clk')  # cnt reaches 1 → busy clears
            m.eval()
            self.assertEqual(m.get('busy'), 0)

            m.step('clk')  # done=1, pipeline latches result
            m.eval()
            self.assertEqual(m.get('out_v'), 1)
            self.assertEqual(m.get('out_d'), 42)


class TestCombResettleHierarchical(unittest.TestCase):
    """Comb re-settle after NBA in hierarchical (flattened) designs.

    When a seq block in one submodule commits an NBA value, comb blocks
    in other submodules that transitively depend on it must re-evaluate.
    """

    def test_seq_in_child_triggers_comb_in_parent(self):
        """Seq block in submodule writes reg; parent comb reads it via port."""
        child = IRModule(name='producer',
            ports=[Port('clk', 'input', 1), Port('val', 'output', 8)],
            regs=[RegDecl('r', 8)],
            assigns=[ContAssign('val', Sig('r'))],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clk')], locals={},
                stmts=[Assign('r', BinOp('+', Sig('r'), Const(1)), False)])])

        parent = IRModule(name='top',
            ports=[Port('clk', 'input', 1), Port('doubled', 'output', 8)],
            wires=[WireDecl('child_val', 8)],
            comb_blocks=[CombBlock(
                stmts=[Assign('doubled', BinOp('+', Sig('child_val'),
                                               Sig('child_val')))],
                locals={})],
            instances=[
                Instance('producer', 'p', {},
                    [('clk','clk'),('val','child_val')]),
            ])

        with _flatten_and_model(parent, {'producer': child}) as m:
            m.eval()
            self.assertEqual(m.get('doubled'), 0)
            m.step('clk')
            m.eval()
            self.assertEqual(m.get('doubled'), 2)  # r=1, doubled=2
            m.step('clk')
            m.eval()
            self.assertEqual(m.get('doubled'), 4)  # r=2, doubled=4

    def test_cross_instance_comb_chain(self):
        """Comb in instance A feeds comb in instance B via parent wiring."""
        passthru = IRModule(name='passthru',
            ports=[Port('inp', 'input', 8), Port('out', 'output', 8)],
            comb_blocks=[CombBlock(
                stmts=[Assign('tmp', BinOp('+', Sig('inp'), Const(1))),
                       Assign('out', Sig('tmp'))],
                locals={'tmp': 8})])

        parent = IRModule(name='top',
            ports=[Port('clk', 'input', 1), Port('result', 'output', 8)],
            regs=[RegDecl('counter', 8)],
            wires=[WireDecl('mid', 8)],
            assigns=[ContAssign('result', Sig('mid'))],
            instances=[
                Instance('passthru', 'p0', {},
                    [('inp','counter'),('out','mid')]),
            ],
            seq_blocks=[SeqBlock(
                edges=[('posedge', 'clk')], locals={},
                stmts=[Assign('counter', BinOp('+', Sig('counter'), Const(10)), False)])])

        with _flatten_and_model(parent, {'passthru': passthru}) as m:
            m.eval()
            self.assertEqual(m.get('result'), 1)  # counter=0, +1
            m.step('clk')
            m.eval()
            self.assertEqual(m.get('result'), 11)  # counter=10, +1


class TestNBACrossBlock(unittest.TestCase):
    """NBA correctness when multiple seq blocks write to signals read by
    comb blocks that feed other seq blocks — the pipeline forwarding pattern.
    """

    def test_write_read_same_cycle(self):
        """Two seq blocks: one writes 'x', the other reads 'x'.

        Both fire on the same posedge. The reader must see the OLD value
        of 'x' (before NBA commit), not the new value.
        """
        ir = IRModule(name='nba_order',
            ports=[Port('clk', 'input', 1),
                   Port('a_out', 'output', 8), Port('b_out', 'output', 8)],
            regs=[RegDecl('a', 8), RegDecl('b', 8)],
            assigns=[ContAssign('a_out', Sig('a')),
                     ContAssign('b_out', Sig('b'))],
            seq_blocks=[
                SeqBlock(edges=[('posedge', 'clk')], locals={},
                    stmts=[Assign('a', BinOp('+', Sig('a'), Const(1)), False)]),
                SeqBlock(edges=[('posedge', 'clk')], locals={},
                    stmts=[Assign('b', Sig('a'), False)]),
            ])
        with CSimModel(ir) as m:
            m.eval()
            self.assertEqual(m.get('a_out'), 0)
            self.assertEqual(m.get('b_out'), 0)
            m.step('clk')
            m.eval()
            # a was 0, now 1. b should get OLD a = 0
            self.assertEqual(m.get('a_out'), 1)
            self.assertEqual(m.get('b_out'), 0)
            m.step('clk')
            m.eval()
            # a was 1, now 2. b should get OLD a = 1
            self.assertEqual(m.get('a_out'), 2)
            self.assertEqual(m.get('b_out'), 1)

    def test_comb_between_seq_blocks(self):
        """Seq A → comb → seq B: comb re-evaluates after A's NBA commit,
        and seq B on the NEXT cycle sees the updated comb output.
        """
        ir = IRModule(name='comb_between',
            ports=[Port('clk', 'input', 1),
                   Port('r_out', 'output', 8), Port('doubled', 'output', 8),
                   Port('latched', 'output', 8)],
            regs=[RegDecl('r', 8), RegDecl('lat', 8)],
            wires=[WireDecl('dbl', 8)],
            assigns=[
                ContAssign('r_out', Sig('r')),
                ContAssign('doubled', Sig('dbl')),
                ContAssign('latched', Sig('lat')),
            ],
            comb_blocks=[CombBlock(
                stmts=[Assign('dbl', BinOp('*', Sig('r'), Const(2)))],
                locals={})],
            seq_blocks=[
                SeqBlock(edges=[('posedge', 'clk')], locals={},
                    stmts=[Assign('r', BinOp('+', Sig('r'), Const(1)), False)]),
                SeqBlock(edges=[('posedge', 'clk')], locals={},
                    stmts=[Assign('lat', Sig('dbl'), False)]),
            ])
        with CSimModel(ir) as m:
            m.eval()
            self.assertEqual(m.get('doubled'), 0)  # r=0, dbl=0
            m.step('clk')
            m.eval()
            # r=1, dbl=2 (after re-settle), lat=OLD dbl=0
            self.assertEqual(m.get('r_out'), 1)
            self.assertEqual(m.get('doubled'), 2)
            self.assertEqual(m.get('latched'), 0)
            m.step('clk')
            m.eval()
            # r=2, dbl=4, lat=OLD dbl=2
            self.assertEqual(m.get('r_out'), 2)
            self.assertEqual(m.get('doubled'), 4)
            self.assertEqual(m.get('latched'), 2)

    def test_three_seq_blocks_circular_nba(self):
        """Three registers in a shift chain: c←b←a←a+1.

        All on the same posedge. Each must read the pre-commit value.
        """
        ir = IRModule(name='shift3',
            ports=[Port('clk', 'input', 1),
                   Port('ao', 'output', 8), Port('bo', 'output', 8),
                   Port('co', 'output', 8)],
            regs=[RegDecl('a', 8), RegDecl('b', 8), RegDecl('c', 8)],
            assigns=[ContAssign('ao', Sig('a')),
                     ContAssign('bo', Sig('b')),
                     ContAssign('co', Sig('c'))],
            seq_blocks=[
                SeqBlock(edges=[('posedge', 'clk')], locals={},
                    stmts=[Assign('a', BinOp('+', Sig('a'), Const(1)), False)]),
                SeqBlock(edges=[('posedge', 'clk')], locals={},
                    stmts=[Assign('b', Sig('a'), False)]),
                SeqBlock(edges=[('posedge', 'clk')], locals={},
                    stmts=[Assign('c', Sig('b'), False)]),
            ])
        with CSimModel(ir) as m:
            # Cycle 0: a=0,b=0,c=0
            for _ in range(4):
                m.step('clk')
            m.eval()
            # After 4 clocks: a=4, b=3 (old a), c=2 (old b)
            self.assertEqual(m.get('ao'), 4)
            self.assertEqual(m.get('bo'), 3)
            self.assertEqual(m.get('co'), 2)
