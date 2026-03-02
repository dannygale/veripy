# VeriPy

Python HDL that simulates and generates Verilog from the same source.

Write your hardware modules once in Python. VeriPy lets you simulate them natively, emit synthesizable Verilog, and verify that both paths produce identical results — all from a single description.

## Quick Example

### `@module` (recommended)

```python
from veripy import module, Input, Output, Register, posedge
from veripy.context import comb, always

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

    @always(posedge(clock))
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
from veripy import VeripyTestCase

class TestCounter(VeripyTestCase):
    def create_module(self):
        return counter(width=4)       # or Counter(width=4)

    def test_counting(self):
        @self.always
        def clock():
            self.set(clock=0)
            yield 5
            self.set(clock=1)
            yield 5

        @self.initial
        def stimulus():
            self.set(reset=1, enable=1)
            yield 10
            self.assertEqual(self.out('count'), 0)
            self.set(reset=0)
            for _ in range(5):
                yield 10
            self.assertEqual(self.out('count'), 5)
```

Each `test_*` method automatically runs twice: once in Python simulation, once through iverilog — outputs are compared cycle-by-cycle.

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
veripy formal <file.py>            # emit .sby + Verilog for SymbiYosys
```

## Features

- **Dual-path testing** — one test verifies both Python sim and generated Verilog ([docs](docs/testing.md))
- **Signal types** — `Input`, `Output`, `Register`, `Signal`, `Mem`, bit slicing, concatenation ([docs](docs/signals.md))
- **Lazy expressions** — signal operators return composable `_Expr` objects with width tracking ([docs](docs/signals.md#operators))
- **Sub-modules** — hierarchical composition with automatic port wiring ([docs](docs/guide.md#sub-modules))
- **BlackBox** — port-only wrappers for vendor/external IP instantiation ([docs](docs/guide.md#blackbox))
- **FSM sugar** — declarative state machines ([docs](docs/guide.md#fsm))
- **Formal properties** — `assert_always`, `cover` ([docs](docs/guide.md#formal-properties))
- **Pipelines** — lambda-chain for simple stages, named stages with per-stage stall/flush for CPU pipelines ([docs](docs/guide.md#pipelines))
- **Timing constraints** — SDC output co-located with logic ([docs](docs/guide.md#timing-constraints))
- **Interface bundles** — reusable signal groups, parameterized widths, bulk connect ([docs](docs/guide.md#interfaces))
- **CSR register maps** — define once, generate RTL, C headers, Python drivers, markdown docs ([docs](docs/guide.md#csr-register-maps))
- **Verilog import** — convert existing RTL to VeriPy ([docs](docs/verilog.md))
- **Lint** — undriven outputs, multi-driven signals, missing reset, unused signals, CDC violations, combinational loops ([docs](docs/verilog.md#lint))
- **CDC primitives** — `Synchronizer`, `AsyncFIFO` with gray-code pointers ([docs](docs/guide.md#cdc))
- **Event-driven simulation** — `SimEngine` with proper Verilog scheduling ([docs](docs/testing.md#simengine))
- **Verilator co-simulation** — compile to shared lib, drive from Python via ctypes for 100-1000x speedup
- **Reactive testing** — `yield until(cond)`, `fork`/`join`, constrained random, coverage reporting ([docs](docs/testing.md#reactive-waits))
- **Protocol drivers** — reusable `Driver` base class for transaction-level bus helpers ([docs](docs/testing.md#protocol-drivers))

## Omnibus Example

[`examples/spi_controller.py`](examples/spi_controller.py) — a pipelined SPI master with TX FIFO that exercises every major feature in one design:

| Feature | Where |
|---|---|
| `@module` decorator | `sync_fifo` (FIFO sub-module) |
| Class-based `Module` | `SpiController` |
| FSM | 4-state SPI protocol (IDLE → LOAD → SHIFT → DONE) |
| `Mem` | FIFO buffer storage |
| `Interface` bundle | `SpiBus` (sclk, mosi, miso, cs_n) |
| Sub-modules | FIFO instantiated inside controller |
| Parametric widths | `width`, `fifo_depth`, `clk_div` |
| `assert_always` | CS must be low during SHIFT |
| `cover` | FIFO full reached |
| Timing constraints | `create_clock`, `max_delay` → SDC |
| Dual-path tests | [`tests/test_spi_controller.py`](tests/test_spi_controller.py) |

## Documentation

- **[Guide](docs/guide.md)** — `@module` tutorial: signals, logic blocks, parameters, sub-modules, FSM, pipelines, formal, timing, interfaces
- **[Class-based API](docs/class-api.md)** — the `Module` base class for advanced use cases
- **[Signals](docs/signals.md)** — signal types, operations, slicing, memory arrays
- **[Testing](docs/testing.md)** — dual-path testing, SimEngine, VCD waveforms
- **[Verilog](docs/verilog.md)** — import, export, emission, lint
