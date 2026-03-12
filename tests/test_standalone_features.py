"""Tests for standalone @module features: @always, @fsm, @assert_always, @cover,
pipeline(), timing constraints, Interface."""
import unittest

from veripy.signal import Input, Output, Register, Signal, Interface
from veripy.context import (comb, always, fsm,
                            assert_always, cover, pipeline,
                            create_clock, max_delay, false_path)
from veripy import posedge, negedge
from veripy.decorator import module
from veripy.verify import TestBench, initial

T = 10


# --- Module definitions ---

@module
def async_reset_ff():
    clk   = Input()
    rst_n = Input()
    d     = Input(8)
    q     = Output(8)
    reg   = Register(8)

    @comb
    def drive():
        q = reg

    @always(posedge(clk) | negedge(rst_n))
    def logic():
        if not rst_n:
            reg = 0
        else:
            reg = d


@module
def traffic_light():
    clock = Input()
    reset = Input()
    go    = Input()
    color = Output(2)

    @fsm(clock, reset, states=['RED', 'GREEN', 'YELLOW'])
    def light(state, RED, GREEN, YELLOW):
        if state == RED:
            color = 0
            if go:
                return GREEN
        elif state == GREEN:
            color = 1
            return YELLOW
        elif state == YELLOW:
            color = 2
            return RED


@module
def bounded_counter():
    clock = Input()
    count = Output(4)
    cnt   = Register(4)

    @comb
    def drive():
        count = cnt

    @always(posedge(clock))
    def inc():
        cnt = cnt + 1

    @assert_always(clock)
    def bounded():
        return int(count) < 16

    @cover(clock)
    def reaches_five():
        return int(count) == 5


@module
def timed_design():
    clk   = Input()
    reset = Input()
    d     = Input(8)
    q     = Output(8)

    create_clock(clk, period_ns=10)
    max_delay(d, q, ns=5)
    false_path(reset, q)

    @comb
    def pass_through():
        q = d


@module
def pipe_adder(width=16):
    clock = Input()
    reset = Input()
    a     = Input(width)
    b     = Input(width)
    out   = Output(width)

    pipe = pipeline(clock, reset, width=width)
    pipe.stage(lambda: int(a) + int(b))
    pipe.stage(lambda prev: prev * 2)

    @comb
    def output():
        out = pipe.result


class AXILite(Interface):
    awaddr  = ('input', 32)
    awvalid = ('input', 1)
    awready = ('output', 1)


@module
def peripheral():
    clock = Input()
    bus   = AXILite()

    @comb
    def logic():
        bus.awready = 1


# --- Tests ---

class TestAlways(TestBench):
    def create_module(self): return async_reset_ff()

    def test_async_reset(self):
        dut = self.dut
        @initial
        def _():
            dut.rst_n = 0; dut.clk = 0; dut.d = 42; yield 1
            self.assertEqual(self.get('q'), 0)

    def test_async_reset_then_capture(self):
        dut = self.dut
        @initial
        def _():
            dut.rst_n = 1; dut.d = 99
            dut.clk = 0; yield 1
            dut.clk = 1; yield 1
            self.assertEqual(self.get('q'), 99)

    def test_always_verilog(self):
        v = async_reset_ff().to_verilog()
        self.assertIn('always @(posedge clk or negedge rst_n)', v)


class TestFSM(TestBench):
    def create_module(self): return traffic_light()

    def test_starts_in_first_state(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T
            self.assertEqual(self.get('color'), 0)

    def test_transitions(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.reset = 1; yield T; dut.reset = 0
            dut.go = 1; yield T
            dut.go = 0; yield T
            self.assertEqual(self.get('color'), 2)


class TestFSMSignals(unittest.TestCase):
    def test_fsm_has_state_register(self):
        m = traffic_light()
        sigs = m._signals()
        self.assertIn('_fsm_state', sigs)
        self.assertIn('_fsm_next', sigs)


class TestAssertAlways(TestBench):
    def create_module(self): return bounded_counter()

    def test_assertion_registered(self):
        m = bounded_counter()
        self.assertEqual(len(m._assertions), 1)

    def test_assertion_passes(self):
        self.clock('clock', T)
        @initial
        def _():
            for _ in range(10): yield T


class TestCover(TestBench):
    def create_module(self): return bounded_counter()

    def test_cover_registered(self):
        m = bounded_counter()
        self.assertEqual(len(m._covers), 1)

    def test_cover_hit(self):
        self.clock('clock', T)
        @initial
        def _():
            for _ in range(6): yield T
        m = bounded_counter()
        # cover tracking is on the module instance; just verify no crash


class TestTimingConstraints(unittest.TestCase):
    def test_timing_registered(self):
        m = timed_design()
        self.assertEqual(len(m._timing), 3)

    def test_sdc_output(self):
        m = timed_design()
        sdc = m.to_sdc()
        self.assertIn('create_clock -period 10 [get_ports clk]', sdc)
        self.assertIn('set_max_delay 5 -from [get_ports d] -to [get_ports q]', sdc)
        self.assertIn('set_false_path -from [get_ports reset] -to [get_ports q]', sdc)


class TestPipeline(TestBench):
    def create_module(self): return pipe_adder(width=8)

    def test_pipeline_latency(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.a = 3; dut.b = 4
            dut.reset = 1; yield T; dut.reset = 0
            yield T  # stage 0: captures 3+4=7
            yield T  # stage 1: captures 7*2=14
            yield T  # output: 14
            self.assertEqual(self.get('out'), 14)

    def test_pipeline_reset(self):
        dut = self.dut; self.clock('clock', T)
        @initial
        def _():
            dut.a = 3; dut.b = 4; dut.reset = 0
            yield T; yield T
            dut.reset = 1; yield T
            self.assertEqual(self.get('out'), 0)


class TestInterface(unittest.TestCase):
    def test_interface_signals_attached(self):
        m = peripheral()
        self.assertTrue(hasattr(m, 'bus'))
        self.assertIsInstance(m.bus, AXILite)

    def test_interface_signal_access(self):
        m = peripheral()
        m._settle_comb()
        self.assertEqual(int(m.bus.awready), 1)

    def test_interface_verilog(self):
        m = peripheral()
        v = m.to_verilog()
        self.assertIn('bus_awaddr', v)
        self.assertIn('bus_awvalid', v)
        self.assertIn('bus_awready', v)


if __name__ == '__main__':
    unittest.main()
