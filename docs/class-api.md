# Class-Based API

> **Note:** The [`@module` decorator](guide.md) is the recommended API. The class-based API is available for advanced use cases requiring full control over `__init__`.

## Module Structure

```python
from veripy import Module, Input, Output, Register

class Counter(Module):
    def __init__(self, width=8):
        self.clock   = Input()
        self.reset   = Input()
        self.enable  = Input()
        self.count   = Output(width)
        self.cnt     = Register(width)
        super().__init__()

        @self.comb
        def drive():
            self.count = self.cnt

        @self.posedge(self.clock)
        def increment():
            if self.reset:
                self.cnt = 0
            elif self.enable:
                self.cnt = self.cnt + 1
```

Signals are declared as `self.` attributes. `super().__init__()` must be called after all signals are defined. Logic blocks are registered via decorators on `self`.

## Logic Block Decorators

| Decorator | Description |
|-----------|-------------|
| `@self.comb` | Combinational logic (`assign` or `always @(*)`) |
| `@self.posedge(signal)` | Sequential logic (`always @(posedge ...)`) |
| `@self.negedge(signal)` | Sequential logic on falling edge |
| `@self.always(sensitivity)` | Explicit sensitivity list |
| `@self.fsm(clk, rst, states=[...])` | State machine |
| `@self.assert_always(clk)` | Formal assertion |
| `@self.cover(clk)` | Coverage point |

## Sub-Modules

```python
class Datapath(Module):
    def __init__(self, width=16):
        self.clock  = Input()
        self.a      = Input(width)
        self.result = Output(width)
        self.alu    = ALU(width)
        super().__init__()

        @self.comb
        def wire():
            self.alu.a = self.a
            self.result = self.alu.result
```

## Parameterization

Constructor arguments become Verilog parameters automatically:

```python
alu = ALU(width=16)
print(alu.to_verilog())  # module alu #(parameter width = 16) (...)
```

## Sensitivity Lists

```python
from veripy import posedge, negedge

@self.always(posedge(self.clk) | negedge(self.rst_n))
def async_reset():
    if not self.rst_n:
        self.reg = 0
    else:
        self.reg = self.d
```

## FSM Sugar

```python
@self.fsm(self.clock, self.reset, states=['IDLE', 'RUN', 'DONE'])
def ctrl(state, IDLE, RUN, DONE):
    if state == IDLE:
        if self.start:
            return RUN
    elif state == RUN:
        return DONE
    elif state == DONE:
        self.done = 1
        return IDLE
```

## Pipelines

```python
pipe = self.pipeline(self.clock, self.reset, width=16)
pipe.stage(lambda: int(self.a) + int(self.b))
pipe.stage(lambda prev: prev * 2)

@self.comb
def output():
    self.out = pipe.result
```

## Formal Properties

```python
@self.assert_always(self.clock)
def bounded():
    return int(self.count) < 16

@self.cover(self.clock)
def reaches_max():
    return int(self.count) == 15
```

## Timing Constraints

```python
self.create_clock(self.clk, period_ns=10)
self.max_delay(self.d, self.q, ns=5)
self.false_path(self.reset, self.q)
print(self.to_sdc())
```

## Interface Bundles

```python
from veripy.signal import Interface

class AXILite(Interface):
    awaddr  = ('input', 32)
    awvalid = ('input', 1)
    awready = ('output', 1)

class Peripheral(Module):
    def __init__(self):
        self.clock = Input()
        self.bus   = AXILite()
        super().__init__()
```

## Python API

```python
m = Counter(width=4)
m.to_verilog()                          # single module Verilog
m.to_sdc()                             # SDC timing constraints
```
