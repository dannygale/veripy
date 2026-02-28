# VeriPy

Python HDL that simulates and generates Verilog from the same source.

Write your hardware modules once in Python. VeriPy lets you simulate them natively, emit synthesizable Verilog, and verify that both paths produce identical results — all from a single description.

## Quick Example

### `@module` (recommended)

```python
from veripy import module, Input, Output, Register
from veripy.context import comb, posedge

@module
def counter(width=8):
    clock   = Input()
    reset   = Input()
    enable  = Input()
    count   = Output(width)
    cnt     = Register(width)

    @comb
    def drive_output():
        count = cnt

    @posedge(clock)
    def increment():
        if reset:
            cnt = 0
        elif enable:
            cnt = cnt + 1
```

### Class-based

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
        def drive_output():
            self.count = self.cnt

        @self.posedge(self.clock)
        def increment():
            if self.reset:
                self.cnt = 0
            elif self.enable:
                self.cnt = self.cnt + 1
```

Both APIs produce identical simulation results and Verilog output. Use whichever you prefer — `@module` is more concise, the class-based API gives full control over `__init__`.

```python
c = counter(width=4)       # or Counter(width=4)
c.enable.set(1)
c.reset.set(1)
c.tick()
c.reset.set(0)
for _ in range(5):
    c.tick()
print(c.count)              # 5
print(c.to_verilog())       # synthesizable Verilog
```

## Installation

Requires Python 3.10+ and [Icarus Verilog](https://steveicarus.github.io/iverilog/) for build verification and dual-path testing.

```
pip install -e .
```

## CLI

```
veripy build <file.py>              # emit Verilog (compile-checked via iverilog)
veripy build <file.py> -o out/      # write .v files to a directory
veripy build <file.py> -p width=4   # pass parameters
veripy test [path] [-v]             # dual-path test suite
veripy import <file.v>              # convert Verilog → VeriPy Python
veripy import rtl/ -o src/          # convert entire project
veripy lint <file.py>               # static checks
```

## Features

- **Dual-path testing** — one test verifies both Python sim and generated Verilog ([docs](docs/testing.md))
- **Signal types** — `Input`, `Output`, `Register`, `Signal`, `Mem`, bit slicing, concatenation ([docs](docs/signals.md))
- **Sub-modules** — hierarchical composition with automatic port wiring ([docs](docs/guide.md#sub-modules))
- **FSM sugar** — declarative state machines ([docs](docs/guide.md#fsm))
- **Formal properties** — `assert_always`, `cover` ([docs](docs/guide.md#formal-properties))
- **Pipelines** — explicit stage boundaries with automatic registers ([docs](docs/guide.md#pipelines))
- **Timing constraints** — SDC output co-located with logic ([docs](docs/guide.md#timing-constraints))
- **Interface bundles** — reusable signal groups, parameterized widths, bulk connect ([docs](docs/guide.md#interfaces))
- **Verilog import** — convert existing RTL to VeriPy ([docs](docs/verilog.md))
- **Lint** — undriven outputs, multi-driven signals, missing reset, unused signals, CDC violations ([docs](docs/verilog.md#lint))
- **CDC primitives** — `Synchronizer`, `AsyncFIFO` with gray-code pointers ([docs](docs/guide.md#cdc))
- **Event-driven simulation** — `SimEngine` with proper Verilog scheduling ([docs](docs/testing.md#simengine))

## Documentation

- **[Guide](docs/guide.md)** — `@module` tutorial: signals, logic blocks, parameters, sub-modules, FSM, pipelines, formal, timing, interfaces
- **[Class-based API](docs/class-api.md)** — the `Module` base class for advanced use cases
- **[Signals](docs/signals.md)** — signal types, operations, slicing, memory arrays
- **[Testing](docs/testing.md)** — dual-path testing, SimEngine, VCD waveforms
- **[Verilog](docs/verilog.md)** — import, export, emission, lint
