#!/usr/bin/env python3
"""Stress test: C-native vs Verilator-native on a larger design.

Builds a 4-channel SPI hub — 4 SPI controllers with mux/demux logic
and a round-robin arbiter. ~200+ signals after flattening.
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register, Signal, VeripyTestCase
from veripy.backend_csim import compile_bench
from veripy.backend_verilator import compile_verilator_bench
from veripy.lower import lower_tb_block
from veripy.ir import IRModule, InitialBlock, AlwaysBlock, Finish
from examples.spi_controller import SpiController


class SpiHub(Module):
    """4-channel SPI controller with round-robin arbiter."""
    def __init__(self, width=8):
        self.clock    = Input()
        self.reset    = Input()
        self.ch_sel   = Input(2)
        self.tx_data  = Input(width)
        self.tx_valid = Input()
        self.tx_ready = Output()
        self.rx_data  = Output(width)
        self.rx_valid = Output()
        self.spi_miso = Input()

        # 4 SPI controllers as named sub-modules
        self.spi0 = SpiController(width=width, fifo_depth=4, clk_div=2)
        self.spi1 = SpiController(width=width, fifo_depth=4, clk_div=2)
        self.spi2 = SpiController(width=width, fifo_depth=4, clk_div=2)
        self.spi3 = SpiController(width=width, fifo_depth=4, clk_div=2)

        # Arbiter state
        self.active_ch   = Register(2)
        self.arb_state   = Register(2)
        self._rx_data_r  = Register(width)
        self._rx_valid_r = Register()

        super().__init__()

        # Wire clocks and resets
        @self.comb
        def wire_common():
            self.spi0.clock = self.clock
            self.spi0.reset = self.reset
            self.spi0.spi.miso = self.spi_miso
            self.spi1.clock = self.clock
            self.spi1.reset = self.reset
            self.spi1.spi.miso = self.spi_miso
            self.spi2.clock = self.clock
            self.spi2.reset = self.reset
            self.spi2.spi.miso = self.spi_miso
            self.spi3.clock = self.clock
            self.spi3.reset = self.reset
            self.spi3.spi.miso = self.spi_miso

        # Demux tx to selected channel
        @self.comb
        def demux_tx():
            self.spi0.tx_data = 0
            self.spi0.tx_valid = 0
            self.spi1.tx_data = 0
            self.spi1.tx_valid = 0
            self.spi2.tx_data = 0
            self.spi2.tx_valid = 0
            self.spi3.tx_data = 0
            self.spi3.tx_valid = 0
            if self.ch_sel == 0:
                self.spi0.tx_data = self.tx_data
                self.spi0.tx_valid = self.tx_valid
            elif self.ch_sel == 1:
                self.spi1.tx_data = self.tx_data
                self.spi1.tx_valid = self.tx_valid
            elif self.ch_sel == 2:
                self.spi2.tx_data = self.tx_data
                self.spi2.tx_valid = self.tx_valid
            else:
                self.spi3.tx_data = self.tx_data
                self.spi3.tx_valid = self.tx_valid

        # Arbiter
        @self.posedge(self.clock)
        def arbiter():
            if self.reset:
                self.active_ch = 0
                self.arb_state = 0
                self._rx_valid_r = 0
                self._rx_data_r = 0
            else:
                self._rx_valid_r = 0
                if self.spi0.rx_valid:
                    self._rx_data_r = self.spi0.rx_data
                    self._rx_valid_r = 1
                elif self.spi1.rx_valid:
                    self._rx_data_r = self.spi1.rx_data
                    self._rx_valid_r = 1
                elif self.spi2.rx_valid:
                    self._rx_data_r = self.spi2.rx_data
                    self._rx_valid_r = 1
                elif self.spi3.rx_valid:
                    self._rx_data_r = self.spi3.rx_data
                    self._rx_valid_r = 1

                if self.arb_state == 0:
                    if self.tx_valid:
                        self.active_ch = self.ch_sel
                        self.arb_state = 1
                elif self.arb_state == 1:
                    if self.spi0.rx_valid or self.spi1.rx_valid or \
                       self.spi2.rx_valid or self.spi3.rx_valid:
                        self.arb_state = 0

        # Output mux
        @self.comb
        def outputs():
            self.rx_data = self._rx_data_r
            self.rx_valid = self._rx_valid_r
            self.tx_ready = self.spi0.tx_ready


T = 10

def build_tb(n):
    class B(VeripyTestCase):
        def create_module(self):
            return SpiHub()
    tc = B('runTest'); tc._begin(); m = tc._mod
    @tc.always
    def clock():
        tc.set(clock=0); yield T
        tc.set(clock=1); yield T
    @tc.initial
    def stim():
        m.reset.set(1); m.tx_valid.set(0); m.tx_data.set(0)
        m.spi_miso.set(0); m.ch_sel.set(0)
        yield T * 2
        m.reset.set(0); yield T * 4
        for i in range(n):
            m.ch_sel.set(i & 3)
            m.tx_data.set(i & 0xFF); m.tx_valid.set(1)
            yield T * 2
            m.tx_valid.set(0)
            for _ in range(200):
                yield T * 2
                if int(m.rx_valid):
                    tc.out('rx_data')
                    break
    mod_var = 'm'
    for fn in tc._tb_initial + tc._tb_always:
        if hasattr(fn, '__code__') and fn.__closure__:
            for i, name in enumerate(fn.__code__.co_freevars):
                if fn.__closure__[i].cell_contents is m:
                    mod_var = name; break
        if mod_var != 'm': break
    tb_ir = IRModule(name='tb')
    for fn in tc._tb_always:
        tb_ir.always_blocks.append(AlwaysBlock(lower_tb_block(fn, m, mod_var, tc._output_names)))
    for fn in tc._tb_initial:
        stmts = lower_tb_block(fn, m, mod_var, tc._output_names)
        stmts.append(Finish())
        tb_ir.initial_blocks.append(InitialBlock(stmts))
    return tb_ir


if __name__ == '__main__':
    from veripy.lower import lower_module
    from veripy.flatten import flatten_ir
    from veripy.emit_verilog import _to_snake
    from veripy.module import Module as _Module

    m = SpiHub()
    registry = {}
    def _collect(mod, mname):
        if mname in registry: return
        factory = getattr(type(mod), '_veripy_factory', None)
        fresh = factory() if factory else type(mod)()
        for sn, sub in fresh._submodules().items():
            _collect(sub, _to_snake(type(sub).__name__))
        registry[mname] = lower_module(fresh, mname)
    for k in dir(m):
        v = getattr(m, k)
        if isinstance(v, _Module) and v is not m:
            _collect(v, _to_snake(type(v).__name__))
    top_ir = lower_module(m, 'spihub')
    flat = flatten_ir(top_ir, registry)
    n_sigs = len(flat.ports) + len(flat.wires) + len(flat.regs)
    n_comb = len(flat.assigns) + len(flat.comb_blocks)
    n_seq = len(flat.seq_blocks)
    n_mem = len(flat.mems)
    print(f'Design: SpiHub (4x SPI controller + arbiter)')
    print(f'  {n_sigs} signals, {n_comb} comb blocks, {n_seq} seq blocks, {n_mem} memories')
    print()

    for N in [1_000, 10_000, 100_000]:
        print(f'--- {N:,} transfers ---')
        tb = build_tb(N)

        m1 = SpiHub()
        run_c, ct_c, cleanup_c = compile_bench(m1, tb, 'spihub')

        m2 = SpiHub()
        run_v, ct_v, cleanup_v = compile_verilator_bench(m2, tb, 'spihub')

        t0 = time.perf_counter()
        run_c()
        t_c = time.perf_counter() - t0

        t0 = time.perf_counter()
        run_v()
        t_v = time.perf_counter() - t0

        print(f'  C-native:    compile {ct_c:.3f}s  exec {t_c:.4f}s  total {ct_c+t_c:.3f}s')
        print(f'  Vltr-native: compile {ct_v:.3f}s  exec {t_v:.4f}s  total {ct_v+t_v:.3f}s')
        print(f'  Exec ratio (Vltr/C): {t_v/t_c:.2f}x    Total ratio: {(ct_v+t_v)/(ct_c+t_c):.1f}x')
        print()

        cleanup_c(); cleanup_v()
