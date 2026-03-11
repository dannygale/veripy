#!/usr/bin/env python3
"""Benchmark: CySim-C vs CSim vs Verilator on SpiHub (4x SPI + arbiter).

CySim-C: model cached as .so, TB lowered to C — all-C execution
CSim:    model + TB compiled to C monolith
Verilator: reference
"""

import sys, os, time, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register, Signal, VeripyTestCase
from veripy.backend_csim import compile_cysim_bench, compile_bench
from veripy.backend_verilator import compile_verilator_bench
from veripy.lower import lower_tb_block
from veripy.ir import IRModule, InitialBlock, AlwaysBlock, Finish
from examples.spi_controller import SpiController

T = 10

if not shutil.which('verilator'):
    print('verilator not found on PATH'); sys.exit(1)

if not shutil.which('verilator'):
    print('verilator not found on PATH'); sys.exit(1)


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
        self.spi0 = SpiController(width=width, fifo_depth=4, clk_div=2)
        self.spi1 = SpiController(width=width, fifo_depth=4, clk_div=2)
        self.spi2 = SpiController(width=width, fifo_depth=4, clk_div=2)
        self.spi3 = SpiController(width=width, fifo_depth=4, clk_div=2)
        self.active_ch   = Register(2)
        self.arb_state   = Register(2)
        self._rx_data_r  = Register(width)
        self._rx_valid_r = Register()
        super().__init__()

        @self.comb
        def wire_common():
            self.spi0.clock = self.clock; self.spi0.reset = self.reset; self.spi0.spi.miso = self.spi_miso
            self.spi1.clock = self.clock; self.spi1.reset = self.reset; self.spi1.spi.miso = self.spi_miso
            self.spi2.clock = self.clock; self.spi2.reset = self.reset; self.spi2.spi.miso = self.spi_miso
            self.spi3.clock = self.clock; self.spi3.reset = self.reset; self.spi3.spi.miso = self.spi_miso

        @self.comb
        def demux_tx():
            self.spi0.tx_data = 0; self.spi0.tx_valid = 0
            self.spi1.tx_data = 0; self.spi1.tx_valid = 0
            self.spi2.tx_data = 0; self.spi2.tx_valid = 0
            self.spi3.tx_data = 0; self.spi3.tx_valid = 0
            if self.ch_sel == 0:
                self.spi0.tx_data = self.tx_data; self.spi0.tx_valid = self.tx_valid
            elif self.ch_sel == 1:
                self.spi1.tx_data = self.tx_data; self.spi1.tx_valid = self.tx_valid
            elif self.ch_sel == 2:
                self.spi2.tx_data = self.tx_data; self.spi2.tx_valid = self.tx_valid
            else:
                self.spi3.tx_data = self.tx_data; self.spi3.tx_valid = self.tx_valid

        @self.posedge(self.clock)
        def arbiter():
            if self.reset:
                self.active_ch = 0; self.arb_state = 0
                self._rx_valid_r = 0; self._rx_data_r = 0
            else:
                self._rx_valid_r = 0
                if self.spi0.rx_valid:
                    self._rx_data_r = self.spi0.rx_data; self._rx_valid_r = 1
                elif self.spi1.rx_valid:
                    self._rx_data_r = self.spi1.rx_data; self._rx_valid_r = 1
                elif self.spi2.rx_valid:
                    self._rx_data_r = self.spi2.rx_data; self._rx_valid_r = 1
                elif self.spi3.rx_valid:
                    self._rx_data_r = self.spi3.rx_data; self._rx_valid_r = 1
                if self.arb_state == 0:
                    if self.tx_valid:
                        self.active_ch = self.ch_sel; self.arb_state = 1
                elif self.arb_state == 1:
                    if self.spi0.rx_valid or self.spi1.rx_valid or \
                       self.spi2.rx_valid or self.spi3.rx_valid:
                        self.arb_state = 0

        @self.comb
        def outputs():
            self.rx_data = self._rx_data_r
            self.rx_valid = self._rx_valid_r
            self.tx_ready = self.spi0.tx_ready


def make_module():
    return SpiHub()


def build_tb_ir(n):
    class _TC(VeripyTestCase):
        def create_module(self): return make_module()
    tc = _TC('runTest'); tc._begin(); m = tc._mod

    @tc.always
    def clk():
        tc.set(clock=0); yield T
        tc.set(clock=1); yield T

    @tc.initial
    def stim():
        m.reset.set(1); m.tx_valid.set(0); m.tx_data.set(0)
        m.spi_miso.set(0); m.ch_sel.set(0)
        yield T * 2; m.reset.set(0); yield T * 4
        for i in range(n):
            m.ch_sel.set(i & 3)
            m.tx_data.set(i & 0xFF); m.tx_valid.set(1)
            yield T * 2; m.tx_valid.set(0)
            for _ in range(200):
                yield T * 2
                if int(m.rx_valid): tc.out('rx_data'); break

    mod_var = 'm'
    for fn in tc._tb_initial + tc._tb_always:
        if hasattr(fn, '__code__') and fn.__closure__:
            for i, name in enumerate(fn.__code__.co_freevars):
                try:
                    if fn.__closure__[i].cell_contents is m:
                        mod_var = name; break
                except ValueError:
                    pass
        if mod_var != 'm': break

    tb_ir = IRModule(name='tb')
    for fn in tc._tb_always:
        tb_ir.always_blocks.append(AlwaysBlock(lower_tb_block(fn, m, mod_var, tc._output_names)))
    for fn in tc._tb_initial:
        stmts = lower_tb_block(fn, m, mod_var, tc._output_names)
        stmts.append(Finish())
        tb_ir.initial_blocks.append(InitialBlock(stmts))
    return tb_ir


def run_cysim_python(n):
    """CySim with Python stimulus — model in C, event loop + stimulus in Python."""
    t0 = time.perf_counter()
    ctx = compile_cysim(make_module(), 'spicontroller')
    compile_t = time.perf_counter() - t0

    with ctx as model:
        engine = ctx.engine()
        engine.clock('clock', T * 2)

        @engine.initial
        def stim():
            model.set('reset', 1); model.set('tx_valid', 0)
            yield T * 2; model.set('reset', 0); yield T * 4
            for i in range(n):
                model.set('tx_data', i & 0xFF); model.set('tx_valid', 1)
                yield T * 2; model.set('tx_valid', 0)
                for _ in range(100):
                    yield T * 2
                    if model.get('rx_valid'): break

        t0 = time.perf_counter()
        engine.run()
        exec_t = time.perf_counter() - t0

    return compile_t, exec_t


if __name__ == '__main__':
    print('SpiHub (4x SPI + arbiter) — CySim-C vs CSim vs Verilator')
    print(f'{"N":>8}  {"CySim-C":>22}  {"CSim":>22}  {"Verilator":>22}')
    print(f'{"":>8}  {"ct":>7} {"exec":>7} {"total":>7}  {"ct":>7} {"exec":>7} {"total":>7}  {"ct":>7} {"exec":>7} {"total":>7}')
    print('-' * 80)

    def fmt(ct, ex): return f'{ct:>6.3f}s {ex:>6.4f}s {ct+ex:>6.3f}s'

    for n in [100, 1_000, 10_000, 100_000]:
        tb_ir = build_tb_ir(n)

        run_cyc, cy_c_ct, cleanup_cyc = compile_cysim_bench(make_module(), tb_ir, 'spihub')
        t0 = time.perf_counter(); run_cyc(); cy_c_exec = time.perf_counter() - t0
        cleanup_cyc()

        run_c, cs_ct, cleanup_c = compile_bench(make_module(), tb_ir, 'spihub')
        t0 = time.perf_counter(); run_c(); cs_exec = time.perf_counter() - t0
        cleanup_c()

        run_v, vt_ct, cleanup_v = compile_verilator_bench(make_module(), tb_ir, 'spihub')
        t0 = time.perf_counter(); run_v(); vt_exec = time.perf_counter() - t0
        cleanup_v()

        print(f'{n:>8,}  {fmt(cy_c_ct, cy_c_exec)}  {fmt(cs_ct, cs_exec)}  {fmt(vt_ct, vt_exec)}', flush=True)
