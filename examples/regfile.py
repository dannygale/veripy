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
