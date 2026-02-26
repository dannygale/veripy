"""veripy — Python HDL that simulates and generates Verilog from the same source."""

from .signal import Signal, Input, Output, Register, SignalArray
from .module import Module
from .emit_verilog import VerilogEmitter
