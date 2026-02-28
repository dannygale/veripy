"""Tests for standalone @module features: @always, @fsm, @assert_always, @cover,
pipeline(), timing constraints, Interface."""
import unittest

from veripy.signal import Input, Output, Register, Signal, Interface
from veripy.context import (comb, always, fsm,
                            assert_always, cover, pipeline,
                            create_clock, max_delay, false_path)
from veripy import posedge, negedge
from veripy.decorator import module
from veripy.sim import SimEngine

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

class TestAlways(unittest.TestCase):
    """@always with multi-edge sensitivity."""

    def test_async_reset(self):
        m = async_reset_ff()
        sim = SimEngine(m)
        @sim.initial
        def _():
            m.rst_n.set(0); m.clk.set(0); m.d.set(42)
            yield 1
            self.assertEqual(int(m.q), 0)
        sim.run()

    def test_async_reset_then_capture(self):
        m = async_reset_ff()
        sim = SimEngine(m)
        @sim.initial
        def _():
            m.rst_n.set(1); m.d.set(99)
            m.clk.set(0); yield 1
            m.clk.set(1); yield 1
            self.assertEqual(int(m.q), 99)
        sim.run()

    def test_always_verilog(self):
        m = async_reset_ff()
        v = m.to_verilog()
        self.assertIn('always @(posedge clk or negedge rst_n)', v)


class TestFSM(unittest.TestCase):
    """@fsm decorator."""

    def test_starts_in_first_state(self):
        m = traffic_light()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T
            self.assertEqual(int(m.color), 0)  # RED → color=0
        sim.run()

    def test_transitions(self):
        m = traffic_light()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.reset.set(1); yield T; m.reset.set(0)
            m.go.set(1); yield T  # RED → GREEN
            m.go.set(0); yield T  # GREEN → YELLOW
            self.assertEqual(int(m.color), 2)
        sim.run()

    def test_fsm_has_state_register(self):
        m = traffic_light()
        sigs = m._signals()
        self.assertIn('_fsm_state', sigs)
        self.assertIn('_fsm_next', sigs)


class TestAssertAlways(unittest.TestCase):
    """@assert_always decorator."""

    def test_assertion_registered(self):
        m = bounded_counter()
        self.assertEqual(len(m._assertions), 1)

    def test_assertion_passes(self):
        m = bounded_counter()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            for _ in range(10):
                yield T
        sim.run()  # should not raise


class TestCover(unittest.TestCase):
    """@cover decorator."""

    def test_cover_registered(self):
        m = bounded_counter()
        self.assertEqual(len(m._covers), 1)

    def test_cover_hit(self):
        m = bounded_counter()
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            for _ in range(6):
                yield T
        sim.run()
        self.assertTrue(m._covers[0][2][0])


class TestTimingConstraints(unittest.TestCase):
    """Timing constraint functions."""

    def test_timing_registered(self):
        m = timed_design()
        self.assertEqual(len(m._timing), 3)

    def test_sdc_output(self):
        m = timed_design()
        sdc = m.to_sdc()
        self.assertIn('create_clock -period 10 [get_ports clk]', sdc)
        self.assertIn('set_max_delay 5 -from [get_ports d] -to [get_ports q]', sdc)
        self.assertIn('set_false_path -from [get_ports reset] -to [get_ports q]', sdc)


class TestPipeline(unittest.TestCase):
    """pipeline() in @module context."""

    def test_pipeline_latency(self):
        m = pipe_adder(width=8)
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.a.set(3); m.b.set(4)
            m.reset.set(1); yield T; m.reset.set(0)
            yield T  # stage 0: 3+4=7
            yield T  # stage 1: 7*2=14
            self.assertEqual(int(m.out), 14)
        sim.run()

    def test_pipeline_reset(self):
        m = pipe_adder(width=8)
        sim = SimEngine(m)
        sim.clock(m.clock, T)
        @sim.initial
        def _():
            m.a.set(3); m.b.set(4); m.reset.set(0)
            yield T; yield T
            m.reset.set(1); yield T
            self.assertEqual(int(m.out), 0)
        sim.run()


class TestInterface(unittest.TestCase):
    """Interface bundles in @module."""

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
