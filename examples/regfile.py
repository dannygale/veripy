"""Register file: 2-read, 1-write register file using Mem."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Mem


class RegFile(Module):
    """4-entry register file. Reads are combinational, writes are clocked.
    Register 0 is hardwired to zero (write ignored)."""

    def __init__(self, width=8, depth=4):
        self.clock = Input()
        self.we    = Input()           # write enable
        self.waddr = Input(2)          # write address
        self.wdata = Input(width)      # write data
        self.raddr1 = Input(2)         # read port 1 address
        self.raddr2 = Input(2)         # read port 2 address
        self.rdata1 = Output(width)    # read port 1 data
        self.rdata2 = Output(width)    # read port 2 data
        self.regs   = Mem(depth, width)
        super().__init__()

        @self.comb
        def read_ports():
            self.rdata1 = self.regs[self.raddr1]
            self.rdata2 = self.regs[self.raddr2]

        @self.posedge(self.clock)
        def write_port():
            if self.we and self.waddr:  # skip writes to r0
                self.regs.write(self.waddr, self.wdata)
