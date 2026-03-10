"""Register file: 2-read, 1-write register file using Mem."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Mem


class RegFile(Module):
    """4-entry register file. Reads are combinational, writes are clocked.
    Register 0 is hardwired to zero (write ignored)."""

    def __init__(self, width=8, depth=4):
        self.clock = Input()
        self.we    = Input()
        self.waddr = Input(2)
        self.wdata = Input(width)
        self.raddr1 = Input(2)
        self.raddr2 = Input(2)
        self.rdata1 = Output(width)
        self.rdata2 = Output(width)
        self.regs   = Mem(depth, width)
        super().__init__()

    def rtl(self):
        @self.comb
        def read_ports():
            self.rdata1 = self.regs[self.raddr1]
            self.rdata2 = self.regs[self.raddr2]

        @self.posedge(self.clock)
        def write_port():
            if self.we and self.waddr:
                self.regs.write(self.waddr, self.wdata)


# ── Inline TestBench ─────────────────────────────────────────────────

from veripy.verify import TestBench


class RegFileTestBench(TestBench):

    def create_module(self):
        return RegFile(width=8, depth=4)

    def test_write_then_read(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(we=1, waddr=1, wdata=42)
            yield 10
            self.set(we=0, raddr1=1)
            yield 10
            self.assertEqual(self.out('rdata1'), 42)

        self.run_sim()

    def test_r0_hardwired_zero(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(we=1, waddr=0, wdata=99)
            yield 10
            self.set(we=0, raddr1=0)
            yield 10
            self.assertEqual(self.out('rdata1'), 0)

        self.run_sim()

    def test_two_read_ports(self):
        self.clock('clock', 10)

        @self.initial
        def stim():
            self.set(we=1, waddr=1, wdata=10)
            yield 10
            self.set(waddr=2, wdata=20)
            yield 10
            self.set(we=0, raddr1=1, raddr2=2)
            yield 10
            self.assertEqual(self.out('rdata1'), 10)
            self.assertEqual(self.out('rdata2'), 20)

        self.run_sim()
