"""Dual-path tests for the hazard unit port."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'examples'))

from hazard_unit import HazardUnit
from veripy import VeripyTestCase


def _defaults():
    return dict(ex_mem_valid=0, ex_mem_reg_we=0, ex_mem_rd_addr=0,
                ex_mem_mem_re=0, mem_wb_valid=0, mem_wb_reg_we=0,
                mem_wb_rd_addr=0, id_ex_rs1_addr=0, id_ex_rs2_addr=0,
                id_rs1_addr=0, id_rs2_addr=0,
                id_ex_valid=0, id_ex_mem_re=0, id_ex_reg_we=0,
                id_ex_cmp=0, id_ex_rd_addr=0,
                id_branch_reg=0, id_branch_flag=0, branch_taken=0)


class TestEXForwarding(VeripyTestCase):
    def create_module(self):
        return HazardUnit()

    def test_no_forward(self):
        @self.initial
        def stim():
            self.set(**_defaults())
            yield 1
            self.assertEqual(self.out('fwd_a'), 0)
            self.assertEqual(self.out('fwd_b'), 0)
        self.run_sim()

    def test_ex_mem_forward_a(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_rd_addr=3,
                     id_ex_rs1_addr=3)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('fwd_a'), 1)
            self.assertEqual(self.out('fwd_b'), 0)
        self.run_sim()

    def test_mem_wb_forward_b(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(mem_wb_valid=1, mem_wb_reg_we=1, mem_wb_rd_addr=5,
                     id_ex_rs2_addr=5)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('fwd_b'), 2)
        self.run_sim()

    def test_ex_mem_priority(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_rd_addr=3,
                     mem_wb_valid=1, mem_wb_reg_we=1, mem_wb_rd_addr=3,
                     id_ex_rs1_addr=3)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('fwd_a'), 1)
        self.run_sim()


class TestIDForwarding(VeripyTestCase):
    def create_module(self):
        return HazardUnit()

    def test_id_fwd_from_ex_mem(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_rd_addr=2,
                     id_rs1_addr=2)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('id_fwd_rs1'), 1)
        self.run_sim()

    def test_id_fwd_blocked_by_load(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(ex_mem_valid=1, ex_mem_reg_we=1, ex_mem_mem_re=1,
                     ex_mem_rd_addr=2, id_rs1_addr=2)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('id_fwd_rs1'), 0)
        self.run_sim()

    def test_id_fwd_from_mem_wb(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(mem_wb_valid=1, mem_wb_reg_we=1, mem_wb_rd_addr=4,
                     id_rs1_addr=4)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('id_fwd_rs1'), 2)
        self.run_sim()


class TestStall(VeripyTestCase):
    def create_module(self):
        return HazardUnit()

    def test_load_use_rs1(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(id_ex_valid=1, id_ex_mem_re=1, id_ex_rd_addr=3,
                     id_rs1_addr=3)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('stall'), 1)
            self.assertEqual(self.out('flush_id_ex'), 1)
        self.run_sim()

    def test_load_use_rs2(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(id_ex_valid=1, id_ex_mem_re=1, id_ex_rd_addr=5,
                     id_rs2_addr=5)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('stall'), 1)
        self.run_sim()

    def test_no_load_use_when_invalid(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(id_ex_valid=0, id_ex_mem_re=1, id_ex_rd_addr=3,
                     id_rs1_addr=3)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('stall'), 0)
        self.run_sim()

    def test_branch_reg_stall_ex(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(id_branch_reg=1, id_ex_valid=1, id_ex_reg_we=1,
                     id_ex_rd_addr=2, id_rs1_addr=2)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('stall'), 1)
        self.run_sim()

    def test_branch_reg_stall_mem_load(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(id_branch_reg=1, ex_mem_valid=1, ex_mem_mem_re=1,
                     ex_mem_rd_addr=2, id_rs1_addr=2)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('stall'), 1)
        self.run_sim()

    def test_branch_flag_stall(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(id_branch_flag=1, id_ex_valid=1, id_ex_cmp=1)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('stall'), 1)
        self.run_sim()

    def test_no_stall(self):
        @self.initial
        def stim():
            d = _defaults()
            self.set(**d)
            yield 1
            self.assertEqual(self.out('stall'), 0)
            self.assertEqual(self.out('flush_id_ex'), 0)
        self.run_sim()


class TestFlush(VeripyTestCase):
    def create_module(self):
        return HazardUnit()

    def test_branch_flush(self):
        @self.initial
        def stim():
            d = _defaults()
            d.update(branch_taken=1)
            self.set(**d)
            yield 1
            self.assertEqual(self.out('flush_if_id'), 1)
            self.assertEqual(self.out('flush_id_ex'), 0)
        self.run_sim()

    def test_no_flush(self):
        @self.initial
        def stim():
            d = _defaults()
            self.set(**d)
            yield 1
            self.assertEqual(self.out('flush_if_id'), 0)
        self.run_sim()
