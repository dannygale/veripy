"""veripy — Python HDL that simulates and generates Verilog from the same source."""

from .signal import Signal, Input, Output, Register, Mem, DualPortMem, TrueDualPortMem
from .signal import Edge, SensitivityList, Interface, posedge, negedge
from .module import Module
from .blackbox import BlackBox
from .parameter import Parameter
from .decorator import module
from .context import (comb, always, fsm, assert_always, cover, assume, pipeline,
                      create_clock, max_delay, false_path)

from .sim import until
from .driver import Driver
from .cdc import Synchronizer, AsyncFIFO
from .ip import (SyncFifo, EdgeDetector, Debouncer, RoundRobinArbiter,
                 PriorityArbiter, ClockDivider, CreditFlowControl)
from .axi4lite import Axi4LiteBus, Axi4LiteSub
from .csr import Field, Reg, RegisterMap
from .project import Project
from .verify import VeripyTestCase


def VerilatorModel(*args, **kwargs):
    """Lazy-loaded Verilator co-simulation model (requires verilator on PATH)."""
    from .backend_verilator import compile_module
    return compile_module(*args, **kwargs)


def GpuSim(*args, **kwargs):
    """Lazy-loaded GPU simulator (requires ``pip install veripy[gpu]``)."""
    from .gpu_sim import GpuSim as _GpuSim
    return _GpuSim(*args, **kwargs)
