"""BlackBox — port-only module for external/vendor IP instantiation."""

import inspect
from .module import Module


class BlackBox(Module):
    """A module with ports and parameters but no internal logic.

    Use for vendor IP (Xilinx BRAM, PLLs, ASIC memories) that should be
    instantiated in generated Verilog but whose definition lives elsewhere.

    The ``verilog_module_name`` parameter overrides the Verilog module type
    name (default: snake_case of the class name).

    Example::

        class XilinxBRAM(BlackBox):
            def __init__(self, data_width=32, addr_width=10):
                self.clk   = Input()
                self.we    = Input()
                self.addr  = Input(addr_width)
                self.din   = Input(data_width)
                self.dout  = Output(data_width)
                super().__init__(verilog_module_name='RAMB36E2')
    """

    _is_blackbox = True

    def __init__(self, verilog_module_name=None, params=None):
        # Auto-capture params from the subclass constructor frame
        if params is None:
            init = type(self).__init__
            if init is not BlackBox.__init__:
                frame = inspect.currentframe().f_back
                sig = inspect.signature(init)
                params = {
                    name: frame.f_locals[name]
                    for name, p in sig.parameters.items()
                    if name != 'self' and name in frame.f_locals
                }
        super().__init__(params=params)
        if verilog_module_name is not None:
            self._verilog_module_name = verilog_module_name
