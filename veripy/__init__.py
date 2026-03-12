"""veripy — Python HDL that simulates and generates Verilog from the same source."""

from .signal import Signal, Input, Output, OutputReg, Register, Mem, DualPortMem, TrueDualPortMem, Inout
from .signal import Edge, SensitivityList, Interface, posedge, negedge
from .signal import clz, ctz, popcount, sext
from .module import Module
from .blackbox import BlackBox
from .parameter import Parameter
from .decorator import module
from .context import (comb, always, fsm, assert_always, cover, assume, pipeline,
                      behavioral, create_clock, max_delay, false_path, clock_domain)

from .sim import until
from .driver import Driver
from .rv import QuickRunMixin
from .cdc import Synchronizer, AsyncFIFO
from .ip import (SyncFifo, EdgeDetector, Debouncer, RoundRobinArbiter,
                 PriorityArbiter, ClockDivider, CreditFlowControl,
                 IntController, DmaEngine,
                 DdrPhy, DdrController, JtagTap, DebugModule)
from .axi4lite import Axi4LiteBus, Axi4LiteSub
from .axi4 import Axi4Bus, Axi4Sub, Axi4Crossbar
from .csr import Field, Reg, RegisterMap
from .project import Project
from .verify import TestBench, initial
from .firmware_test import FirmwareTestCase
from .firmware import ElfSegment, ElfImage, load_elf
from .soc_sim import FlatMemory, UartPeripheral, SocSim
from .gdb_stub import GdbStub

__all__ = [
    # Signals
    "Signal", "Input", "Output", "OutputReg", "Register", "Mem",
    "DualPortMem", "TrueDualPortMem", "Edge", "SensitivityList", "Interface",
    "posedge", "negedge", "clz", "ctz", "popcount", "sext",
    # Modules
    "Module", "BlackBox", "Parameter", "module",
    # Logic blocks
    "comb", "always", "fsm", "assert_always", "cover", "assume", "pipeline",
    "behavioral", "create_clock", "max_delay", "false_path", "clock_domain",
    # Simulation
    "until", "Driver", "QuickRunMixin",
    # CDC / IP
    "Synchronizer", "AsyncFIFO",
    "SyncFifo", "EdgeDetector", "Debouncer", "RoundRobinArbiter",
    "PriorityArbiter", "ClockDivider", "CreditFlowControl",
    "IntController", "DmaEngine", "DdrPhy", "DdrController", "JtagTap", "DebugModule",
    # Bus / CSR
    "Axi4LiteBus", "Axi4LiteSub", "Axi4Bus", "Axi4Sub", "Axi4Crossbar",
    "Field", "Reg", "RegisterMap",
    # Testing
    "TestBench", "initial",
    "FirmwareTestCase",
    # Firmware / SoC
    "ElfSegment", "ElfImage", "load_elf",
    "FlatMemory", "UartPeripheral", "SocSim", "GdbStub",
    # Project
    "Project",
    # Lazy backends
    "VerilatorModel", "CSimModel", "GpuSim",
]


def VerilatorModel(*args, **kwargs):
    """Lazy-loaded Verilator co-simulation model (requires verilator on PATH)."""
    from .backend_verilator import compile_module
    return compile_module(*args, **kwargs)


def CSimModel(*args, **kwargs):
    """Lazy-loaded native C simulation model (requires cc on PATH)."""
    from .backend_csim import compile_module
    return compile_module(*args, **kwargs)


def GpuSim(*args, **kwargs):
    """Lazy-loaded GPU simulator (requires ``pip install veripy[gpu]``)."""
    from .gpu_sim import GpuSim as _GpuSim
    return _GpuSim(*args, **kwargs)
