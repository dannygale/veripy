"""Tests for IP library: SyncFifo, EdgeDetector, Debouncer, arbiters, ClockDivider, CreditFlowControl."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy import (SyncFifo, EdgeDetector, Debouncer,
                    RoundRobinArbiter, PriorityArbiter, ClockDivider,
                    CreditFlowControl, IntController, DmaEngine,
                    DdrPhy, DdrController, JtagTap, DebugModule)
from veripy.verify import TestBench, initial

T = 5  # half-period


class TestSyncFifoBasic(TestBench):
    def create_module(self): return SyncFifo(width=8, depth=4)

    def test_push_pop(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.push = 0; dut.pop = 0; dut.din = 0
            yield T * 4
            dut.reset = 0; yield T * 2
            self.assertEqual(self.get('empty'), 1)
            self.assertEqual(self.get('full'), 0)
            dut.push = 1; dut.din = 0x42; yield T * 2
            dut.push = 0; yield T * 2
            self.assertEqual(self.get('empty'), 0)
            self.assertEqual(self.get('dout'), 0x42)
            dut.pop = 1; yield T * 2
            dut.pop = 0; yield T * 2
            self.assertEqual(self.get('empty'), 1)


class TestSyncFifoFull(TestBench):
    def create_module(self): return SyncFifo(width=8, depth=4)

    def test_fill_to_full(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.push = 0; dut.pop = 0; dut.din = 0
            yield T * 4
            dut.reset = 0; yield T * 2
            for i in range(4):
                dut.push = 1; dut.din = i + 1; yield T * 2
            dut.push = 0; yield T * 2
            self.assertEqual(self.get('full'), 1)
            self.assertEqual(self.get('count'), 4)


class TestEdgeDetector(TestBench):
    def create_module(self): return EdgeDetector()

    def test_rising_edge(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.d = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.d = 1; yield T * 2
            self.assertEqual(self.get('rise'), 1)
            self.assertEqual(self.get('fall'), 0)
            yield T * 2
            self.assertEqual(self.get('rise'), 0)

    def test_falling_edge(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.d = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.d = 1; yield T * 4
            dut.d = 0; yield T * 2
            self.assertEqual(self.get('fall'), 1)
            self.assertEqual(self.get('rise'), 0)


class TestDebouncer(TestBench):
    def create_module(self): return Debouncer(threshold=3)

    def test_stable_input_passes(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.d = 0; yield T * 4
            dut.reset = 0; yield T * 2
            self.assertEqual(self.get('q'), 0)
            dut.d = 1
            for _ in range(4): yield T * 2
            self.assertEqual(self.get('q'), 1)

    def test_glitch_rejected(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.d = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.d = 1; yield T * 2
            dut.d = 0; yield T * 4
            self.assertEqual(self.get('q'), 0)


class TestPriorityArbiter(TestBench):
    def create_module(self): return PriorityArbiter(n=4)

    def test_lowest_index_wins(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.req = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.req = 0b0101; yield T * 2
            self.assertEqual(self.get('grant'), 0b0001)

    def test_single_high_bit(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.req = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.req = 0b1000; yield T * 2
            self.assertEqual(self.get('grant'), 0b1000)

    def test_no_request(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.req = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.req = 0; yield T * 2
            self.assertEqual(self.get('grant'), 0)


class TestClockDivider(TestBench):
    def create_module(self): return ClockDivider(divisor=2)

    def test_divides_clock(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; yield T * 4
            dut.reset = 0; yield T * 2
            self.assertEqual(self.get('clk_out'), 0)
            yield T * 2; yield T * 2
            self.assertEqual(self.get('clk_out'), 1)
            yield T * 2; yield T * 2
            self.assertEqual(self.get('clk_out'), 0)


class TestCreditFlowControl(TestBench):
    def create_module(self): return CreditFlowControl(credits=2)

    def test_initial_credits(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.send_valid = 0; dut.recv_valid = 0
            yield T * 4; dut.reset = 0; yield T * 2
            self.assertEqual(self.get('send_ready'), 1)

    def test_credits_exhaust(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.send_valid = 0; dut.recv_valid = 0
            yield T * 4; dut.reset = 0; yield T * 2
            dut.send_valid = 1
            yield T * 2; yield T * 2
            dut.send_valid = 0; yield T * 2
            self.assertEqual(self.get('send_ready'), 0)

    def test_credit_return(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.send_valid = 0; dut.recv_valid = 0
            yield T * 4; dut.reset = 0; yield T * 2
            dut.send_valid = 1; yield T * 2; yield T * 2
            dut.send_valid = 0; yield T * 2
            self.assertEqual(self.get('send_ready'), 0)
            dut.recv_valid = 1; yield T * 2
            dut.recv_valid = 0; yield T * 2
            self.assertEqual(self.get('send_ready'), 1)


class TestRoundRobinArbiter(TestBench):
    def create_module(self): return RoundRobinArbiter(n=4)

    def test_single_request(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.req = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.req = 0b0001; yield T * 2
            self.assertEqual(self.get('grant'), 0b0001)

    def test_round_robin_rotation(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.req = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.req = 0b1111; yield T * 2
            self.assertEqual(self.get('grant'), 0b0001)
            yield T * 2
            self.assertEqual(self.get('grant'), 0b0010)
            yield T * 2
            self.assertEqual(self.get('grant'), 0b0100)

    def test_no_request(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.req = 0; yield T * 4
            dut.reset = 0; yield T * 2
            dut.req = 0; yield T * 2
            self.assertEqual(self.get('grant'), 0)


class TestIPVerilog(unittest.TestCase):
    def test_syncfifo_emits(self):
        v = SyncFifo(width=8, depth=4).to_verilog()
        self.assertIn('module syncfifo', v)
        self.assertIn('always @(posedge clock)', v)
        self.assertIn('mem', v)

    def test_edgedetector_emits(self):
        v = EdgeDetector().to_verilog()
        self.assertIn('module edgedetector', v)
        self.assertIn('rise', v)
        self.assertIn('fall', v)

    def test_debouncer_emits(self):
        v = Debouncer(threshold=8).to_verilog()
        self.assertIn('module debouncer', v)
        self.assertIn('threshold', v)

    def test_arbiter_emits(self):
        v = RoundRobinArbiter(n=4).to_verilog()
        self.assertIn('module roundrobinarbiter', v)
        self.assertIn('grant', v)
        self.assertIn('req', v)

    def test_priority_arbiter_emits(self):
        v = PriorityArbiter(n=4).to_verilog()
        self.assertIn('module priorityarbiter', v)
        self.assertIn('grant', v)
        self.assertIn('req', v)

    def test_clockdivider_emits(self):
        v = ClockDivider(divisor=4).to_verilog()
        self.assertIn('module clockdivider', v)
        self.assertIn('clk_out', v)

    def test_creditflowcontrol_emits(self):
        v = CreditFlowControl(credits=4).to_verilog()
        self.assertIn('module creditflowcontrol', v)
        self.assertIn('send_ready', v)
        self.assertIn('recv_ready', v)


class TestIntControllerBasic(TestBench):
    def create_module(self): return IntController(n=4)

    def test_irq_sets_pending(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.irq = 0; dut.ier_we = 0; dut.ier_wdata = 0; dut.ipr_clr = 0
            yield T * 4; dut.reset = 0; yield T * 2
            dut.ier_we = 1; dut.ier_wdata = 0b0001; yield T * 2
            dut.ier_we = 0; yield T * 2
            self.assertEqual(self.get('irq_out'), 0)
            dut.irq = 0b0001; yield T * 2
            dut.irq = 0; yield T * 2
            self.assertEqual(self.get('ipr') & 1, 1)
            self.assertEqual(self.get('irq_out'), 1)
            dut.ipr_clr = 0b0001; yield T * 2
            dut.ipr_clr = 0; yield T * 2
            self.assertEqual(self.get('irq_out'), 0)
            self.assertEqual(self.get('ipr') & 1, 0)

    def test_disabled_irq_no_output(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.irq = 0; dut.ier_we = 0; dut.ier_wdata = 0; dut.ipr_clr = 0
            yield T * 4; dut.reset = 0; yield T * 2
            dut.irq = 0b0001; yield T * 2
            dut.irq = 0; yield T * 2
            self.assertEqual(self.get('ipr') & 1, 1)
            self.assertEqual(self.get('irq_out'), 0)


class TestDmaEngine(TestBench):
    def create_module(self): return DmaEngine(addr_width=16, data_width=32)

    def test_single_word_transfer(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.start = 0; dut.src_addr = 0; dut.dst_addr = 0x100
            dut.length = 0; dut.rd_data = 0; dut.rd_valid = 0; dut.wr_ready = 0
            yield T * 4; dut.reset = 0; yield T * 2
            dut.start = 1; dut.src_addr = 0x10; dut.dst_addr = 0x20; dut.length = 1
            yield T * 2; dut.start = 0; yield T * 2
            self.assertEqual(self.get('rd_en'), 1)
            self.assertEqual(self.get('rd_addr'), 0x10)
            self.assertEqual(self.get('busy'), 1)
            dut.rd_data = 0xABCD1234; dut.rd_valid = 1; yield T * 2
            dut.rd_valid = 0; yield T * 2
            self.assertEqual(self.get('wr_en'), 1)
            self.assertEqual(self.get('wr_addr'), 0x20)
            self.assertEqual(self.get('wr_data'), 0xABCD1234)
            dut.wr_ready = 1; yield T * 2
            dut.wr_ready = 0
            self.assertEqual(self.get('done'), 1)
            self.assertEqual(self.get('busy'), 1)
            yield T * 2
            self.assertEqual(self.get('busy'), 0)
            self.assertEqual(self.get('done'), 0)

    def test_multi_word_transfer(self):
        dut = self.dut; self.clock('clock', T * 2)
        @initial
        def _():
            dut.reset = 1; dut.start = 0; dut.src_addr = 0; dut.dst_addr = 0
            dut.length = 0; dut.rd_data = 0; dut.rd_valid = 0; dut.wr_ready = 0
            yield T * 4; dut.reset = 0; yield T * 2
            dut.start = 1; dut.src_addr = 0; dut.dst_addr = 0x100; dut.length = 3
            yield T * 2; dut.start = 0
            words = [0x11111111, 0x22222222, 0x33333333]
            for i, w in enumerate(words):
                yield T * 2
                self.assertEqual(self.get('rd_en'), 1)
                self.assertEqual(self.get('rd_addr'), i * 4)
                dut.rd_data = w; dut.rd_valid = 1; yield T * 2
                dut.rd_valid = 0; yield T * 2
                self.assertEqual(self.get('wr_en'), 1)
                self.assertEqual(self.get('wr_addr'), 0x100 + i * 4)
                self.assertEqual(self.get('wr_data'), w)
                dut.wr_ready = 1; yield T * 2
                dut.wr_ready = 0
            self.assertEqual(self.get('done'), 1)
            yield T * 2
            self.assertEqual(self.get('busy'), 0)


class TestDdrPhy(unittest.TestCase):
    def test_is_blackbox(self):
        from veripy.blackbox import BlackBox
        self.assertIsInstance(DdrPhy(), BlackBox)

    def test_verilog_name_override(self):
        phy = DdrPhy(verilog_module_name='MIG_7SERIES')
        self.assertEqual(phy._verilog_module_name, 'MIG_7SERIES')

    def test_default_verilog_name(self):
        phy = DdrPhy()
        self.assertEqual(phy._verilog_module_name, 'ddr_phy')

    def test_ports_created(self):
        phy = DdrPhy(data_width=16)
        sigs = phy._signals()
        self.assertIn('sys_clk', sigs)
        self.assertIn('init_done', sigs)
        self.assertIn('app_rdy', sigs)


class TestDdrController(unittest.TestCase):
    def test_is_module(self):
        from veripy.module import Module
        self.assertIsInstance(DdrController(), Module)

    def test_ports(self):
        ctrl = DdrController()
        sigs = ctrl._signals()
        for name in ('clock', 'reset', 'ready', 'addr', 'we', 'wdata', 're', 'rdata', 'rvalid'):
            self.assertIn(name, sigs, f'missing: {name}')

    def test_phy_submodule(self):
        ctrl = DdrController()
        from veripy.blackbox import BlackBox
        self.assertIsInstance(ctrl.phy, BlackBox)


class TestJtagTap(unittest.TestCase):
    def test_is_blackbox(self):
        from veripy.blackbox import BlackBox
        self.assertIsInstance(JtagTap(), BlackBox)

    def test_verilog_name_override(self):
        tap = JtagTap(verilog_module_name='BSCANE2')
        self.assertEqual(tap._verilog_module_name, 'BSCANE2')

    def test_ports_created(self):
        tap = JtagTap()
        sigs = tap._signals()
        for name in ('tck', 'tms', 'tdi', 'tdo', 'dbg_addr', 'dbg_we',
                     'dbg_wdata', 'dbg_rdata', 'dbg_valid'):
            self.assertIn(name, sigs, f'missing: {name}')


class TestDebugModule(unittest.TestCase):
    def test_is_module(self):
        from veripy.module import Module
        self.assertIsInstance(DebugModule(), Module)

    def test_ports(self):
        dm = DebugModule()
        sigs = dm._signals()
        for name in ('tck', 'tms', 'tdi', 'tdo', 'clock', 'reset',
                     'dbg_addr', 'dbg_we', 'dbg_wdata', 'dbg_rdata'):
            self.assertIn(name, sigs, f'missing: {name}')

    def test_tap_submodule(self):
        dm = DebugModule()
        from veripy.blackbox import BlackBox
        self.assertIsInstance(dm.tap, BlackBox)


if __name__ == '__main__':
    unittest.main()
