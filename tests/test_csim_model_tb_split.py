"""Tests for csim model/testbench split compilation (compile_model / compile_tb)."""
import os
import tempfile
import unittest

from veripy import Module, Input, Output, Register
from veripy.ir import (
    IRModule, Port, RegDecl, ContAssign, SeqBlock, CombBlock,
    Assign, Delay, Const, Sig, If, BinOp,
    AlwaysBlock, InitialBlock,
)
from veripy.backend_csim import compile_model, compile_tb, emit_c_header


class Counter(Module):
    def __init__(self, width=4):
        self.clock  = Input()
        self.reset  = Input()
        self.enable = Input()
        self.count  = Output(width)
        self.cnt    = Register(width)
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


def _make_tb_ir(half_period=5, run_cycles=20):
    """Build a minimal tb_ir: clock toggle + reset/enable stimulus."""
    tb = IRModule(name='tb', ports=[], wires=[], regs=[], mems=[], assigns=[],
                  comb_blocks=[], seq_blocks=[], instances=[], params={})
    tb.always_blocks = [AlwaysBlock(stmts=[
        Assign('clock', Const(0)),
        Delay(Const(half_period)),
        Assign('clock', Const(1)),
        Delay(Const(half_period)),
    ])]
    tb.initial_blocks = [InitialBlock(stmts=[
        Assign('reset', Const(1)),
        Assign('enable', Const(1)),
        Delay(Const(half_period * 2)),
        Assign('reset', Const(0)),
        Delay(Const(half_period * 2 * run_cycles)),
    ])]
    return tb


class TestCompileModel(unittest.TestCase):

    def test_returns_lib_path_and_ir(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            lib_path, flat_ir, model_c_src, header_src = compile_model(
                Counter(), 'counter', cache_dir=cache_dir)
            self.assertTrue(os.path.exists(lib_path))
            self.assertIn('State', model_c_src)
            self.assertIn('veripy_create', model_c_src)
            self.assertIsNotNone(flat_ir)

    def test_cache_hit_returns_same_path(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            path1, _, _, _ = compile_model(Counter(), 'counter', cache_dir=cache_dir)
            path2, _, _, _ = compile_model(Counter(), 'counter', cache_dir=cache_dir)
            self.assertEqual(path1, path2)

    def test_different_params_different_lib(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            path4, _, _, _ = compile_model(Counter(width=4), 'counter4', cache_dir=cache_dir)
            path8, _, _, _ = compile_model(Counter(width=8), 'counter8', cache_dir=cache_dir)
            self.assertNotEqual(path4, path8)


class TestEmitCHeader(unittest.TestCase):

    def test_header_contains_state_struct(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            _, flat_ir, model_c_src, header_src = compile_model(
                Counter(), 'counter', cache_dir=cache_dir)
        self.assertIn('typedef struct {', header_src)
        self.assertIn('} State;', header_src)

    def test_header_contains_extern_decls(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            _, flat_ir, model_c_src, header_src = compile_model(
                Counter(), 'counter', cache_dir=cache_dir)
        self.assertIn('extern void*', header_src)
        self.assertIn('veripy_create', header_src)
        self.assertIn('extern void', header_src)
        self.assertIn('veripy_destroy', header_src)
        self.assertIn('veripy_eval', header_src)
        self.assertIn('veripy_set_clock', header_src)
        self.assertIn('veripy_get_count', header_src)


class TestCompileTb(unittest.TestCase):

    def test_compile_and_run(self):
        tb_ir = _make_tb_ir()
        with tempfile.TemporaryDirectory() as cache_dir:
            lib_path, flat_ir, model_c_src, _ = compile_model(
                Counter(), 'counter', cache_dir=cache_dir)
            run, compile_t, cleanup = compile_tb(
                tb_ir, lib_path, model_c_src, flat_ir, half_period=5)
            try:
                self.assertGreater(compile_t, 0)
                run()  # should not raise
            finally:
                cleanup()

    def test_model_reuse_across_tb_compilations(self):
        """Model .so is compiled once; two TB compilations reuse it."""
        with tempfile.TemporaryDirectory() as cache_dir:
            lib_path, flat_ir, model_c_src, _ = compile_model(
                Counter(), 'counter', cache_dir=cache_dir)
            mtime = os.path.getmtime(lib_path)

            run1, _, cleanup1 = compile_tb(_make_tb_ir(run_cycles=10),
                                           lib_path, model_c_src, flat_ir)
            run2, _, cleanup2 = compile_tb(_make_tb_ir(run_cycles=20),
                                           lib_path, model_c_src, flat_ir)
            try:
                run1()
                run2()
                # Model .so must not have been recompiled
                self.assertEqual(mtime, os.path.getmtime(lib_path))
            finally:
                cleanup1()
                cleanup2()


if __name__ == '__main__':
    unittest.main()
