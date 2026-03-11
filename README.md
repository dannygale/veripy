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
from veripy.verify import TestBench, initial

class TestCounter(TestBench):
    def create_module(self):
        return counter(width=4)       # or Counter(width=4)

    def test_counting(self):
        dut = self.dut
        self.clock('clock', period=10)

        @initial
        def stimulus():
            dut.reset = 1; dut.enable = 1
            yield 10
            assert dut.count == 0
            dut.reset = 0
            for _ in range(5):
                yield 10
            assert dut.count == 5
```

Each `test_*` method automatically runs against all available model tiers (functional, cycle, RTL) on the cysim backend — a Cython-compiled C simulation — and cross-checks that all models produce identical outputs.

## Installation

Requires Python 3.10+ and [Icarus Verilog](https://steveicarus.github.io/iverilog/) for build verification and dual-path testing.

```
pip install -e .
```

## CLI

```
design:
  veripy build [file.py]            # emit Verilog (compile-checked via iverilog)
  veripy lint [file.py]             # static checks
  veripy doc [file.py]              # generate markdown documentation
  veripy graph <file.py>            # emit DOT block diagram of module hierarchy
  veripy stats <file.py>            # print design statistics

verification:
  veripy test [path] [-v] [-j]      # run test suite (cysim default)
  veripy check [file.py]            # behavioral vs RTL equivalence (Hypothesis)
  veripy formal [file.py]           # emit .sby + Verilog for SymbiYosys
  veripy equiv gold.py gate.py      # formal equivalence checking (Yosys)
  veripy profile <test_file.py>     # compare backend performance

targets:
  veripy fpga build [file.py]       # synthesize to FPGA bitstream
  veripy soc build <config.yaml>    # build SoC from config

project:
  veripy init <name>                # scaffold a new project with veripy.toml
  veripy ip list|init <name>        # manage IP packages
  veripy import <file.v>            # convert Verilog → VeriPy Python
  veripy run <script.py>            # run script with project on sys.path
```

Commands marked `[file.py]` are optional when `veripy.toml` is present.

## Features

- **Multi-tier testing** — `TestBench` runs each test against all model tiers (functional, cycle, RTL) and cross-checks outputs. `FirmwareTestCase` for ELF/CPU tests ([docs](docs/testing.md))
- **Cython-compiled simulation (cysim)** — default backend: Cython-compiled event loop with direct C model execution, 40-120× faster than Python sim ([docs](docs/testing.md#cysim))
- **Signal namespace** — `self.dut` for clean test syntax: `dut.reset = 0` sets, `dut.count` reads ([docs](docs/testing.md#testbench--functional-testing))
- **Batch execution** — `self.run_cycles(n)` runs N clock cycles entirely in compiled C ([docs](docs/testing.md#testbench-api))
- **Behavioral ↔ RTL equivalence** — `veripy check` fuzzes `@behavioral` vs RTL automatically via Hypothesis ([docs](docs/testing.md#behavioral--rtl-equivalence-checking))
- **Native C simulation (csim)** — csim backend compiles IR to C for near-Verilator speed with sub-second compile times
- **Signal types** — `Input`, `Output`, `Register`, `Signal`, `Mem`, bit slicing, concatenation ([docs](docs/signals.md))
- **Lazy expressions** — signal operators return composable `_Expr` objects with width tracking ([docs](docs/signals.md#operators))
- **Parameters** — `Parameter`, `ParamExpr`, `clog2()` for parametric widths with Verilog emission ([docs](docs/signals.md#parameters-and-parametric-widths))
- **Sub-modules** — hierarchical composition with automatic port wiring ([docs](docs/guide.md#sub-modules))
- **BlackBox** — port-only wrappers for vendor/external IP instantiation ([docs](docs/guide.md#blackbox))
- **Behavioral models** — `@behavioral` decorator for Python-only simulation models alongside RTL ([docs](docs/guide.md#behavioral--python-only-simulation-model))
- **FSM sugar** — declarative state machines ([docs](docs/guide.md#fsm))
- **Formal properties** — `assert_always`, `cover` ([docs](docs/guide.md#formal-properties))
- **Pipelines** — lambda-chain for simple stages, named stages with per-stage stall/flush for CPU pipelines ([docs](docs/guide.md#pipelines))
- **Timing constraints** — SDC output co-located with logic ([docs](docs/guide.md#timing-constraints))
- **Interface bundles** — reusable signal groups, parameterized widths, bulk connect ([docs](docs/guide.md#interfaces))
- **CSR register maps** — define once, generate RTL, C headers, Python drivers, markdown docs ([docs](docs/guide.md#csr-register-maps))
- **Verilog import** — convert existing RTL to VeriPy ([docs](docs/verilog.md))
- **Lint** — undriven outputs, multi-driven signals, missing reset, unused signals, CDC violations, combinational loops ([docs](docs/verilog.md#lint))
- **CDC primitives** — `Synchronizer`, `AsyncFIFO` with gray-code pointers ([docs](docs/guide.md#cdc))
- **IP library** — `SyncFifo`, `EdgeDetector`, `Debouncer`, `RoundRobinArbiter`, `PriorityArbiter`, `ClockDivider`, `CreditFlowControl` ([docs](docs/guide.md#ip-library))
- **IP packaging** — pip-installable IP packages with `veripy ip` CLI
- **Event-driven simulation** — `SimEngine` with proper Verilog scheduling ([docs](docs/testing.md#simengine))
- **Verilator co-simulation** — compile to shared lib, drive from Python via ctypes for 100-1000x speedup
- **GPU simulation** — batch-parallel verification on WebGPU, thousands of instances per dispatch ([docs](docs/gpu.md))
- **Reactive testing** — `yield until(cond)`, `fork`/`join`, constrained random, coverage reporting ([docs](docs/testing.md#reactive-waits))
- **Protocol drivers** — reusable `Driver` base class for transaction-level bus helpers ([docs](docs/testing.md#protocol-drivers))
- **Equivalence checking** — formal equivalence via Yosys ([docs](docs/equiv.md))
- **Auto-documentation** — generate markdown docs from module definitions
- **Project configuration** — `veripy.toml` for project settings, `veripy init` for scaffolding

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
- **[Signals](docs/signals.md)** — signal types, operations, slicing, memory arrays, parameters
- **[Testing](docs/testing.md)** — multi-tier testing, SimEngine, VCD waveforms, cysim, protocol drivers
- **[Verilog](docs/verilog.md)** — import, export, emission, lint
- **[Equivalence Checking](docs/equiv.md)** — formal equivalence via Yosys equiv_check
- **[GPU Simulation](docs/gpu.md)** — WGPU backend, batch-parallel verification, flattening
