"""Tests for the hazard unit port."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'examples'))

from hazard_unit import HazardUnit
from veripy.verify import TestBench, initial


def _defaults():
    return dict(ex_mem_valid=0, ex_mem_reg_we=0, ex_mem_rd_addr=0,
                ex_mem_mem_re=0, mem_wb_valid=0, mem_wb_reg_we=0,
                mem_wb_rd_addr=0, id_ex_rs1_addr=0, id_ex_rs2_addr=0,
                id_rs1_addr=0, id_rs2_addr=0,
                id_ex_valid=0, id_ex_mem_re=0, id_ex_reg_we=0,
                id_ex_cmp=0, id_ex_rd_addr=0,
                id_branch_reg=0, id_branch_flag=0, branch_taken=0)


class TestEXForwarding(TestBench):
    def create_module(self): return HazardUnit()

    def test_no_forward(self):
        @initial
        def _():
            self.set(**_defaults()); yield 1
            self.assertEqual(self.get('fwd_a'), 0)
            self.assertEqual(self.get('fwd_b'), 0)

    def test_ex_mem_forward_a(self):
        @initial
        def _():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_rd_addr=3, id_ex_rs1_addr=3)
            self.set(**d); yield 1
            self.assertEqual(self.get('fwd_a'), 1)
            self.assertEqual(self.get('fwd_b'), 0)

    def test_mem_wb_forward_b(self):
        @initial
        def _():
            d = _defaults()
            d.update(mem_wb_valid=1, mem_wb_reg_we=1, mem_wb_rd_addr=5, id_ex_rs2_addr=5)
            self.set(**d); yield 1
            self.assertEqual(self.get('fwd_b'), 2)

    def test_ex_mem_priority(self):
        @initial
        def _():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_rd_addr=3,
                     mem_wb_valid=1, mem_wb_reg_we=1, mem_wb_rd_addr=3, id_ex_rs1_addr=3)
            self.set(**d); yield 1
            self.assertEqual(self.get('fwd_a'), 1)


class TestIDForwarding(TestBench):
    def create_module(self): return HazardUnit()

    def test_id_fwd_from_ex_mem(self):
        @initial
        def _():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_rd_addr=2, id_rs1_addr=2)
            self.set(**d); yield 1
            self.assertEqual(self.get('id_fwd_rs1'), 1)

    def test_id_fwd_blocked_by_load(self):
        @initial
        def _():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_mem_re=1,
                     ex_mem_rd_addr=2, id_rs1_addr=2)
            self.set(**d); yield 1
            self.assertEqual(self.get('id_fwd_rs1'), 0)

    def test_id_fwd_from_mem_wb(self):
        @initial
        def _():
            d = _defaults()
            d.update(mem_wb_valid=1, mem_wb_reg_we=1, mem_wb_rd_addr=4, id_rs1_addr=4)
            self.set(**d); yield 1
            self.assertEqual(self.get('id_fwd_rs1'), 2)


class TestStall(TestBench):
    def create_module(self): return HazardUnit()

    def test_load_use_rs1(self):
        @initial
        def _():
            d = _defaults()
            d.update(id_ex_valid=1, id_ex_mem_re=1, id_ex_rd_addr=3, id_rs1_addr=3)
            self.set(**d); yield 1
            self.assertEqual(self.get('stall'), 1)
            self.assertEqual(self.get('flush_id_ex'), 1)

    def test_load_use_rs2(self):
        @initial
        def _():
            d = _defaults()
            d.update(id_ex_valid=1, id_ex_mem_re=1, id_ex_rd_addr=5, id_rs2_addr=5)
            self.set(**d); yield 1
            self.assertEqual(self.get('stall'), 1)

    def test_no_load_use_when_invalid(self):
        @initial
        def _():
            d = _defaults()
            d.update(id_ex_valid=0, id_ex_mem_re=1, id_ex_rd_addr=3, id_rs1_addr=3)
            self.set(**d); yield 1
            self.assertEqual(self.get('stall'), 0)

    def test_branch_reg_stall_ex(self):
        @initial
        def _():
            d = _defaults()
            d.update(id_branch_reg=1, id_ex_valid=1, id_ex_reg_we=1,
                     id_ex_rd_addr=2, id_rs1_addr=2)
            self.set(**d); yield 1
            self.assertEqual(self.get('stall'), 1)

    def test_branch_reg_stall_mem_load(self):
        @initial
        def _():
            d = _defaults()
            d.update(id_branch_reg=1, ex_mem_valid=1, ex_mem_mem_re=1,
                     ex_mem_rd_addr=2, id_rs1_addr=2)
            self.set(**d); yield 1
            self.assertEqual(self.get('stall'), 1)

    def test_branch_flag_stall(self):
        @initial
        def _():
            d = _defaults()
            d.update(id_branch_flag=1, id_ex_valid=1, id_ex_cmp=1)
            self.set(**d); yield 1
            self.assertEqual(self.get('stall'), 1)

    def test_no_stall(self):
        @initial
        def _():
            self.set(**_defaults()); yield 1
            self.assertEqual(self.get('stall'), 0)
            self.assertEqual(self.get('flush_id_ex'), 0)


class TestFlush(TestBench):
    def create_module(self): return HazardUnit()

    def test_branch_flush(self):
        @initial
        def _():
            d = _defaults(); d.update(branch_taken=1)
            self.set(**d); yield 1
            self.assertEqual(self.get('flush_if_id'), 1)
            self.assertEqual(self.get('flush_id_ex'), 0)

    def test_no_flush(self):
        @initial
        def _():
            self.set(**_defaults()); yield 1
            self.assertEqual(self.get('flush_if_id'), 0)
