"""Dual-path tests: verify Python sim and iverilog agree."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register, VeripyTestCase


class Counter(Module):
    def __init__(self, n=4):
        self.clock = Input()
        self.reset = Input()
        self.enable = Input()
        self.count = Output(n)
        self.counter = Register(n)
        super().__init__()

        @self.comb
        def drive_output():
            self.count <<= self.counter

        @self.posedge(self.clock)
        def increment():
            if self.reset:
                self.counter <<= 0
            elif self.enable:
                self.counter <<= self.counter + 1


class PipeReg(Module):
    def __init__(self, width=8):
        self.clock = Input()
        self.reset = Input()
        self.flush = Input()
        self.stall = Input()
        self.d     = Input(width)
        self.q     = Output(width)
        self.data  = Register(width)
        super().__init__()

        @self.comb
        def drive():
            self.q <<= self.data

        @self.posedge(self.clock)
        def capture():
            if self.reset or self.flush:
                self.data <<= 0
            elif not self.stall:
                self.data <<= self.d


class ForwardMux(Module):
    def __init__(self, width=8):
        self.rs_addr     = Input(3)
        self.ex_rd_addr  = Input(3)
        self.mem_rd_addr = Input(3)
        self.ex_we       = Input()
        self.mem_we      = Input()
        self.reg_val     = Input(width)
        self.ex_val      = Input(width)
        self.mem_val     = Input(width)
        self.out         = Output(width)
        self.fwd_sel     = Output(2)
        super().__init__()

        @self.comb
        def forward():
            if self.ex_we and self.rs_addr == self.ex_rd_addr:
                self.out <<= self.ex_val
                self.fwd_sel <<= 1
            elif self.mem_we and self.rs_addr == self.mem_rd_addr:
                self.out <<= self.mem_val
                self.fwd_sel <<= 2
            else:
                self.out <<= self.reg_val
                self.fwd_sel <<= 0


# --- Dual-path tests ---

class TestCounter(VeripyTestCase):
    def create_module(self):
        return Counter(4)

    def test_reset_then_count(self):
        self.set(reset=1, enable=1)
        self.tick()
        self.assertEqual(self.out('count'), 0)

        self.set(reset=0)
        for i in range(5):
            self.tick()
        self.assertEqual(self.out('count'), 5)

    def test_enable_gating(self):
        self.set(reset=1, enable=0)
        self.tick()
        self.set(reset=0, enable=0)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.out('count'), 0)

        self.set(enable=1)
        self.tick()
        self.assertEqual(self.out('count'), 1)

    def test_overflow(self):
        self.set(reset=1, enable=1)
        self.tick()
        self.set(reset=0)
        for _ in range(16):
            self.tick()
        self.assertEqual(self.out('count'), 0)  # 4-bit wraps


class TestPipeReg(VeripyTestCase):
    def create_module(self):
        return PipeReg(8)

    def test_capture(self):
        self.set(reset=1, flush=0, stall=0, d=0)
        self.tick()
        self.assertEqual(self.out('q'), 0)

        self.set(reset=0, d=0xAB)
        self.tick()
        self.assertEqual(self.out('q'), 0xAB)

    def test_stall(self):
        self.set(reset=1, flush=0, stall=0, d=0)
        self.tick()
        self.set(reset=0, d=0x42)
        self.tick()
        self.assertEqual(self.out('q'), 0x42)

        self.set(stall=1, d=0xFF)
        self.tick()
        self.assertEqual(self.out('q'), 0x42)  # stalled

    def test_flush(self):
        self.set(reset=1, flush=0, stall=0, d=0)
        self.tick()
        self.set(reset=0, d=0x42)
        self.tick()
        self.set(flush=1)
        self.tick()
        self.assertEqual(self.out('q'), 0)


class TestForwardMux(VeripyTestCase):
    def create_module(self):
        return ForwardMux(8)

    def test_no_forward(self):
        self.set(rs_addr=3, ex_rd_addr=5, mem_rd_addr=5,
                 ex_we=0, mem_we=0, reg_val=0x10, ex_val=0xEE, mem_val=0xDD)
        self.tick()
        self.assertEqual(self.out('out'), 0x10)
        self.assertEqual(self.out('fwd_sel'), 0)

    def test_ex_forward(self):
        self.set(rs_addr=3, ex_rd_addr=3, mem_rd_addr=5,
                 ex_we=1, mem_we=0, reg_val=0x10, ex_val=0xEE, mem_val=0xDD)
        self.tick()
        self.assertEqual(self.out('out'), 0xEE)
        self.assertEqual(self.out('fwd_sel'), 1)

    def test_ex_priority(self):
        self.set(rs_addr=3, ex_rd_addr=3, mem_rd_addr=3,
                 ex_we=1, mem_we=1, reg_val=0x10, ex_val=0xEE, mem_val=0xDD)
        self.tick()
        self.assertEqual(self.out('out'), 0xEE)
        self.assertEqual(self.out('fwd_sel'), 1)
