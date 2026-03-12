"""Hierarchical csim tests: reproduce cache-like data corruption patterns."""

import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register, Signal
from veripy.verify import TestBench, initial


class RegStage(Module):
    def __init__(self, width=8):
        self.clk = Input()
        self.din = Input(width)
        self.dout = Output(width)
        self.r = Register(width)
        super().__init__()

        @self.comb
        def out():
            self.dout = self.r

        @self.posedge(self.clk)
        def cap():
            self.r = self.din


class RegSink(Module):
    def __init__(self, width=32):
        self.clk = Input()
        self.din = Input(width)
        self.wen = Input()
        self.waddr = Input(2)
        self.raddr = Input(2)
        self.dout = Output(width)
        self.r0 = Register(width)
        self.r1 = Register(width)
        self.r2 = Register(width)
        self.r3 = Register(width)
        super().__init__()

        @self.comb
        def read():
            if self.raddr == 0:
                self.dout = self.r0
            elif self.raddr == 1:
                self.dout = self.r1
            elif self.raddr == 2:
                self.dout = self.r2
            else:
                self.dout = self.r3

        @self.posedge(self.clk)
        def write():
            if self.wen:
                if self.waddr == 0:
                    self.r0 = self.din
                elif self.waddr == 1:
                    self.r1 = self.din
                elif self.waddr == 2:
                    self.r2 = self.din
                elif self.waddr == 3:
                    self.r3 = self.din


class TwoStage(Module):
    def __init__(self, width=8):
        self.clk = Input()
        self.din = Input(width)
        self.dout = Output(width)
        self.src = RegStage(width)
        self.snk = RegStage(width)
        super().__init__()

        @self.comb
        def wire():
            self.src.clk = self.clk
            self.snk.clk = self.clk
            self.src.din = self.din
            self.snk.din = self.src.dout
            self.dout = self.snk.dout


class TestTwoStage(TestBench):
    def create_module(self): return TwoStage(width=8)

    def test_pipeline_data(self):
        dut = self.dut
        @initial
        def _():
            dut.clk = 0; dut.din = 0; yield 1
            dut.din = 0xAB; dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            dut.din = 0xCD; dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            self.assertEqual(self.get('dout'), 0xAB)
            dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            self.assertEqual(self.get('dout'), 0xCD)

    def test_multiple_values(self):
        dut = self.dut
        vals = [0x11, 0x22, 0x33, 0x44, 0x55]
        @initial
        def _():
            dut.clk = 0; dut.din = 0; yield 1
            for v in vals:
                dut.din = v; dut.clk = 1; yield 1
                dut.clk = 0; yield 1
            self.assertEqual(self.get('dout'), vals[-2])
            dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            self.assertEqual(self.get('dout'), vals[-1])


class RegToSink(Module):
    def __init__(self, width=32):
        self.clk = Input()
        self.din = Input(width)
        self.wen = Input()
        self.waddr = Input(2)
        self.raddr = Input(2)
        self.dout = Output(width)
        self.src = RegStage(width)
        self.snk = RegSink(width)
        super().__init__()

        @self.comb
        def wire():
            self.src.clk = self.clk
            self.snk.clk = self.clk
            self.src.din = self.din
            self.snk.din = self.src.dout
            self.snk.wen = self.wen
            self.snk.waddr = self.waddr
            self.snk.raddr = self.raddr
            self.dout = self.snk.dout


class TestRegToSink(TestBench):
    def create_module(self): return RegToSink(width=32)

    def test_single_write_read(self):
        dut = self.dut
        @initial
        def _():
            dut.clk = 0; dut.din = 0; dut.wen = 0; dut.waddr = 0; dut.raddr = 0; yield 1
            dut.din = 0x42; dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            dut.wen = 1; dut.waddr = 0; dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            dut.wen = 0; dut.raddr = 0; yield 1
            self.assertEqual(self.get('dout'), 0x42)

    def test_sequential_fill(self):
        dut = self.dut
        words = [0xAA, 0xBB, 0xCC, 0xDD]
        @initial
        def _():
            dut.clk = 0; dut.din = 0; dut.wen = 0; dut.waddr = 0; dut.raddr = 0; yield 1
            dut.din = words[0]; dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            for i in range(4):
                next_din = words[i + 1] if i < 3 else 0
                dut.din = next_din; dut.wen = 1; dut.waddr = i; dut.clk = 1; yield 1
                dut.clk = 0; yield 1
            dut.wen = 0
            for i in range(4):
                dut.raddr = i; yield 1
                self.assertEqual(self.get('dout'), words[i])

    def test_fill_32bit(self):
        dut = self.dut
        words = [0x00001117, 0x00010113, 0x05000293, 0x05000313]
        @initial
        def _():
            dut.clk = 0; dut.din = 0; dut.wen = 0; dut.waddr = 0; dut.raddr = 0; yield 1
            dut.din = words[0]; dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            for i in range(4):
                next_din = words[i + 1] if i < 3 else 0
                dut.din = next_din; dut.wen = 1; dut.waddr = i; dut.clk = 1; yield 1
                dut.clk = 0; yield 1
            dut.wen = 0
            for i in range(4):
                dut.raddr = i; yield 1
                self.assertEqual(self.get('dout'), words[i])


class ThreeLevel(Module):
    def __init__(self, width=32):
        self.clk = Input()
        self.din = Input(width)
        self.wen = Input()
        self.waddr = Input(2)
        self.raddr = Input(2)
        self.dout = Output(width)
        self.inner = RegToSink(width)
        super().__init__()

        @self.comb
        def wire():
            self.inner.clk = self.clk
            self.inner.din = self.din
            self.inner.wen = self.wen
            self.inner.waddr = self.waddr
            self.inner.raddr = self.raddr
            self.dout = self.inner.dout


class TestThreeLevel(TestBench):
    def create_module(self): return ThreeLevel(width=32)

    def test_fill_through_3_levels(self):
        words = [0xDEAD, 0xBEEF, 0xCAFE, 0xF00D]
        @initial
        def _():
            dut = self.dut
            dut.clk = 0; dut.wen = 0; dut.waddr = 0; dut.raddr = 0; dut.din = 0; yield 1
            # Pre-load first word into src register
            dut.din = words[0]; dut.clk = 1; yield 1; dut.clk = 0; yield 1
            # Write src.dout to snk at each address, loading next word into src
            for i in range(4):
                next_din = words[i + 1] if i < 3 else 0
                dut.din = next_din; dut.wen = 1; dut.waddr = i
                dut.clk = 1; yield 1; dut.clk = 0; yield 1
            dut.wen = 0
            for i, w in enumerate(words):
                dut.raddr = i; yield 1
                self.assertEqual(self.get('dout'), w)


class DualSink(Module):
    def __init__(self, width=32):
        self.clk = Input()
        self.din = Input(width)
        self.wen_a = Input()
        self.waddr_a = Input(2)
        self.raddr_a = Input(2)
        self.dout_a = Output(width)
        self.wen_b = Input()
        self.waddr_b = Input(2)
        self.raddr_b = Input(2)
        self.dout_b = Output(width)
        self.src = RegStage(width)
        self.a = RegSink(width)
        self.b = RegSink(width)
        super().__init__()

        @self.comb
        def wire():
            self.src.clk = self.clk
            self.a.clk = self.clk
            self.b.clk = self.clk
            self.src.din = self.din
            self.a.din = self.src.dout
            self.b.din = self.src.dout
            self.a.wen = self.wen_a
            self.a.waddr = self.waddr_a
            self.a.raddr = self.raddr_a
            self.dout_a = self.a.dout
            self.b.wen = self.wen_b
            self.b.waddr = self.waddr_b
            self.b.raddr = self.raddr_b
            self.dout_b = self.b.dout


class TestDualSink(TestBench):
    def create_module(self): return DualSink(width=32)

    def test_both_sinks_get_same_data(self):
        dut = self.dut
        words = [0x00001117, 0x00010113, 0x05000293, 0x05000313]
        @initial
        def _():
            dut.clk = 0; dut.din = 0
            dut.wen_a = 0; dut.wen_b = 0
            dut.waddr_a = 0; dut.waddr_b = 0
            dut.raddr_a = 0; dut.raddr_b = 0; yield 1
            dut.din = words[0]; dut.clk = 1; yield 1
            dut.clk = 0; yield 1
            for i in range(4):
                next_din = words[i + 1] if i < 3 else 0
                dut.din = next_din; dut.wen_a = 1; dut.wen_b = 1
                dut.waddr_a = i; dut.waddr_b = i; dut.clk = 1; yield 1
                dut.clk = 0; yield 1
            dut.wen_a = 0; dut.wen_b = 0
            for i in range(4):
                dut.raddr_a = i; dut.raddr_b = i; yield 1
                self.assertEqual(self.get('dout_a'), words[i])
                self.assertEqual(self.get('dout_b'), words[i])
