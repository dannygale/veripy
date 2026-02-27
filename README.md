# VeriPy

Python HDL that simulates and generates Verilog from the same source.

Write your hardware modules once in Python. VeriPy lets you simulate them natively, emit synthesizable Verilog, and verify that both paths produce identical results — all from a single description.

## Quick Example

```python
from veripy import Module, Input, Output, Register

class Counter(Module):
    def __init__(self, n=8):
        self.clock   = Input()
        self.reset   = Input()
        self.enable  = Input()
        self.count   = Output(n)
        self.counter = Register(n)
        super().__init__()

        @self.comb
        def drive_output():
            self.count = self.counter

        @self.posedge(self.clock)
        def increment():
            if self.reset:
                self.counter = 0
            elif self.enable:
                self.counter = self.counter + 1
```

Simulate in Python:

```python
c = Counter(n=4)
c.enable.set(1)
c.reset.set(1)
c.tick()
c.reset.set(0)
for _ in range(5):
    c.tick()
print(c.count)  # 5
```

Generate Verilog:

```
$ veripy build examples/counter.py -p n=4
module counter (
    input clock,
    input enable,
    input reset,
    output [3:0] count
);

    reg [3:0] counter;

    assign count = counter;

    always @(posedge clock) begin
        if (reset) begin
            counter <= 0;
        end else
        if (enable) begin
            counter <= (counter + 1);
        end
    end

endmodule
```

## Installation

Requires Python 3.10+ and [Icarus Verilog](https://steveicarus.github.io/iverilog/) for build verification and dual-path testing.

```
pip install -e .
```

## Quickstart: Importing an Existing Verilog Project

Already have RTL? Convert it to VeriPy in a few steps.

**1. Import your Verilog files**

```
$ veripy import rtl/alu.v -o veripy_src/alu.py
$ veripy import rtl/datapath.v -o veripy_src/datapath.py
```

Or batch-convert a directory:

```bash
for f in rtl/*.v; do
    veripy import "$f" -o "veripy_src/$(basename "${f%.v}.py")"
done
```

**2. Run lint to catch issues**

```
$ veripy lint veripy_src/alu.py
warning: output 'result' is never driven
warning: signal 'tmp' is unused
```

**3. Write a dual-path test**

```python
from veripy import VeripyTestCase
from veripy_src.alu import alu

class TestALU(VeripyTestCase):
    def create_module(self):
        return alu()

    def test_add(self):
        self.set(a=3, b=4, op=0)
        self.tick()
        self.assertEqual(self.out('result'), 7)
```

**4. Verify Python sim matches Verilog**

```
$ veripy test tests/ -v
```

This runs your test against both the Python model and the generated Verilog, comparing outputs cycle-by-cycle. If they diverge, you'll see exactly which signal and cycle differ.

**5. Iterate in Python**

From here you can refactor, add [formal properties](#formal-properties), attach [timing constraints](#timing-annotations-and-sdc-output), or build new modules — all in Python with instant simulation feedback. When you're ready, `veripy build` emits synthesizable Verilog.

## CLI

```
veripy build <file.py>                  # emit Verilog to stdout (compile-checked via iverilog)
veripy build <file.py> -o out/          # write .v files to a directory
veripy build <file.py> -m Counter       # target a specific Module subclass
veripy build <file.py> -p n=4           # pass parameters
veripy test [path] [-v]                 # run dual-path test suite
veripy import <file.v>                  # convert Verilog to VeriPy Python
veripy import <file.v> -o out.py        # write to file
veripy lint <file.py>                   # run static checks on a module
```

`build` always compiles the emitted Verilog through iverilog before outputting. If the generated RTL is broken, you'll see the errors immediately.

`import` parses the veripy Verilog subset (modules, ports, assign, always, case, if/else, sub-module instances with parameter overrides) and emits equivalent VeriPy Python. Handles hierarchy — sub-module instances with parameter overrides and wire-to-port rewriting are preserved. Useful for converting existing RTL to start using VeriPy for simulation and testing.

`lint` detects common RTL mistakes: undriven outputs, multi-driven signals, missing reset, and unused signals. See [Lint / Static Checks](#lint--static-checks).

## Dual-Path Testing

The signature feature: write one test, verify both the Python simulation and the generated Verilog produce the same outputs on every cycle.

```python
from veripy import VeripyTestCase

class TestCounter(VeripyTestCase):
    def create_module(self):
        return Counter(4)

    def test_counting(self):
        self.set(reset=1, enable=1)
        self.tick()
        self.assertEqual(self.out('count'), 0)
        self.set(reset=0)
        for _ in range(5):
            self.tick()
        self.assertEqual(self.out('count'), 5)
```

Each `test_*` method automatically runs three ways:
1. Python simulation — your assertions run against the Python model
2. Sim vs RTL — the recorded stimulus is replayed through iverilog and all outputs are compared cycle-by-cycle
3. Verilog — your assertions run against the iverilog outputs

```
$ veripy test tests/ -v
```

## Signal Types

| Type | Description |
|------|-------------|
| `Input(width)` | Input port (default width 1) |
| `Output(width)` | Output port |
| `Register(width)` | Stateful `reg` signal (non-blocking `<=` as module attribute, blocking `=` as local temporary) |
| `Signal(width)` | Internal wire |
| `Mem(width, depth)` | Memory array with `.write(addr, data)` |
| `Interface` | Reusable signal bundle (see [Interface Bundles](#interface-bundles)) |

All signal types support `.set(value)` for immediate value assignment in testbenches, and `int(signal)` to read the current value.

## Module Structure

Modules define signals in `__init__`, then register logic blocks:

- `@self.comb` — combinational logic (emits `assign` or `always @(*)`)
- `@self.posedge(self.clock)` — sequential logic (emits `always @(posedge clk)`)
- `@self.negedge(self.clock)` — sequential logic on falling edge
- `@self.always(sensitivity)` — explicit sensitivity list (see [Sensitivity Lists](#sensitivity-lists))
- `@self.fsm(clock, reset, states=[...])` — state machine (see [FSM Sugar](#fsm-sugar))
- `@self.assert_always(clock)` — formal assertion (see [Formal Properties](#formal-properties))
- `@self.cover(clock)` — coverage point (see [Formal Properties](#formal-properties))
- `self.pipeline(clock, reset, width=N)` — pipeline registers (see [Pipeline Transforms](#pipeline-transforms))

Sub-modules are declared as attributes and automatically discovered for Verilog emission.

## Sub-Module Instantiation

Assign a `Module` instance as an attribute to wire it as a sub-module. Drive its inputs and read its outputs from `@self.comb` blocks:

```python
class Datapath(Module):
    def __init__(self, width=16):
        self.clock  = Input()
        self.a      = Input(width)
        self.b      = Input(width)
        self.result = Output(width)
        self.alu    = ALU(width)       # sub-module instance
        super().__init__()

        @self.comb
        def wire_alu():
            self.alu.a = self.a        # drive sub-module inputs
            self.alu.b = self.b
            self.alu.op = 0

        @self.comb
        def output():
            self.result = self.alu.result  # read sub-module output
```

The emitter creates wires for each sub-module port and generates an instance with port connections. Use `emit_all()` to emit both parent and child definitions.

Sub-modules with constructor arguments automatically get parameter overrides on the instance, so the same module type can be reused with different configurations:

```python
self.l1 = Cache(size=1024)
self.l2 = Cache(size=4096)
```

Emits:

```verilog
cache #(.size(1024)) l1 ( ... );
cache #(.size(4096)) l2 ( ... );
```

## Signal Operations

Signals support bit slicing, concatenation, and ternary selection — in both simulation and Verilog output.

Bit slicing with `[hi:lo]`:

```python
data = Signal(8)
data.set(0xAB)
upper = data[7:4]   # 0xA
lower = data[3:0]   # 0xB
data[7:4] = 0xF     # data becomes 0xFB
```

Concatenation with list assignment:

```python
self.out = [self.high, self.low]   # {high, low} in Verilog
```

Ternary mux with Python's conditional expression:

```python
@self.comb
def select():
    self.out = self.a if self.sel else self.b
```

Emits: `assign out = sel ? a : b;`

## Mem (Memory Arrays)

`Mem(depth, width)` provides memory arrays with combinational reads and clocked writes:

```python
from veripy import Module, Input, Output, Mem

class RegFile(Module):
    def __init__(self, width=8, depth=4):
        self.clock = Input()
        self.we    = Input()
        self.waddr = Input(2)
        self.wdata = Input(width)
        self.raddr = Input(2)
        self.rdata = Output(width)
        self.regs  = Mem(depth, width)
        super().__init__()

        @self.comb
        def read():
            self.rdata = self.regs[self.raddr]       # combinational read

        @self.posedge(self.clock)
        def write():
            if self.we:
                self.regs.write(self.waddr, self.wdata)  # clocked write
```

Emits `reg [7:0] regs [0:3]` with an `initial` block to zero-initialize, `regs[raddr]` for reads, and `regs[waddr] <= wdata` for writes.

## For-Loop Unrolling

`for ... in range(...)` inside logic blocks is unrolled at emit time with constant folding:

```python
@self.posedge(self.clock)
def shift():
    for i in range(3):
        self.stage[i] = self.stage[i + 1]
```

The emitter expands this into individual assignments (`stage[0] <= stage[1]; stage[1] <= stage[2]; ...`). Only `range()` with constant arguments is supported.

## Sensitivity Lists

For blocks that trigger on multiple edges, use `@self.always` with `posedge` and `negedge`:

```python
from veripy import Module, Input, Output, Register, posedge, negedge

class AsyncReset(Module):
    def __init__(self):
        self.clk   = Input()
        self.rst_n = Input()
        self.d     = Input(8)
        self.q     = Output(8)
        self.reg   = Register(8)
        super().__init__()

        @self.comb
        def drive():
            self.q = self.reg

        @self.always(posedge(self.clk) | negedge(self.rst_n))
        def logic():
            if not self.rst_n:
                self.reg = 0
            else:
                self.reg = self.d
```

Emits: `always @(posedge clk or negedge rst_n)`

## Event-Driven Simulation (SimEngine)

`SimEngine` provides event-driven simulation with proper Verilog scheduling semantics. Testbench blocks are Python generators that `yield` time delays:

```python
from veripy import Module, Input, Output, Register
from veripy.sim import SimEngine

counter = Counter(n=4)
sim = SimEngine(counter)

@sim.initial
def stimulus():
    counter.reset.set(1)
    counter.enable.set(1)
    yield 1
    counter.reset.set(0)
    for _ in range(5):
        yield 1
    assert int(counter.count) == 5

sim.run()
```

`@sim.initial` blocks run once; `@sim.always` blocks restart on completion. The engine processes signal changes through active → NBA → re-settle regions, matching Verilog simulator behavior. Simulation ends when all initial blocks finish or `sim.finish()` is called.

Pass `vcd=` to dump waveforms viewable in GTKWave:

```python
sim = SimEngine(counter, vcd='counter.vcd')
```

## Python API

Generate Verilog programmatically without the CLI:

```python
# Single module
counter = Counter(n=4)
print(counter.to_verilog())

# Hierarchical — emits sub-module definitions followed by the parent
from veripy.emit_verilog import VerilogEmitter
datapath = Datapath()
print(VerilogEmitter(datapath).emit_all())

# SDC timing constraints
print(counter.to_sdc())

# Lint checks
from veripy.lint import lint
for warning in lint(counter):
    print(warning)

# Import Verilog
from veripy.import_verilog import import_verilog
python_code = import_verilog('design.v')
```

`to_verilog()` emits a single module. `emit_all()` walks sub-modules and emits each unique definition, then the parent — suitable for multi-file or concatenated output. `to_sdc()` generates SDC timing constraints from annotations. `lint()` returns a list of warning strings. `import_verilog()` converts a Verilog file to VeriPy Python source.

## Parameterization

Constructor arguments become Verilog parameters when passed via `params`:

```python
class ALU(Module):
    def __init__(self, width=8):
        self.a   = Input(width)
        self.b   = Input(width)
        self.out = Output(width)
        super().__init__()
        # ...
```

From the CLI, `-p` passes parameters and emits a `#(parameter ...)` header:

```
$ veripy build alu.py -p width=16
module alu #(
    parameter width = 16
) (
    input [15:0] a,
    input [15:0] b,
    output [15:0] out
);
```

From Python, constructor arguments are automatically captured as parameters:

```python
alu = ALU(width=16)
print(alu.to_verilog())
```

## Verilog Import

Convert existing Verilog files to VeriPy Python:

```
$ veripy import alu.v
```

```python
from veripy import Module, Input, Output, Register

class alu(Module):
    def __init__(self, width=8):
        self.a   = Input(width)
        self.b   = Input(width)
        self.out = Output(width)
        super().__init__()

        @self.comb
        def _assign_0():
            self.out = self.a + self.b
```

The importer handles:
- Module declarations with parameters
- `assign`, `always @(*)`, `always @(posedge ...)` blocks
- `if`/`else`, `case` statements
- Sub-module instances with parameter overrides (`#(.WIDTH(16))`)
- Hierarchical designs — wire-to-port rewriting is preserved so sub-module connections round-trip correctly

From Python:

```python
from veripy.import_verilog import import_verilog
python_code = import_verilog('design.v')
exec(python_code)  # defines the Module subclass(es)
```

Write to file with `-o`:

```
$ veripy import design.v -o design.py
```

## Lint / Static Checks

Catch common RTL mistakes at Python time:

```
veripy lint examples/counter.py
```

Detects:
- Undriven outputs
- Multi-driven signals
- Missing reset on sequential blocks
- Unused signals

From Python:

```python
from veripy.lint import lint
warnings = lint(MyModule())
for w in warnings:
    print(w)
```

## FSM Sugar

Define state machines declaratively with `@self.fsm`:

```python
class TrafficLight(Module):
    def __init__(self):
        self.clock = Input()
        self.reset = Input()
        self.go    = Input()
        self.color = Output(2)
        super().__init__()

        @self.fsm(self.clock, self.reset, states=['RED', 'GREEN', 'YELLOW'])
        def light(state, RED, GREEN, YELLOW):
            if state == RED:
                self.color = 0
                if self.go:
                    return GREEN
            elif state == GREEN:
                self.color = 1
                return YELLOW
            elif state == YELLOW:
                self.color = 2
                return RED
```

The decorator creates a state register, next-state signal, and emits the FSM as combinational logic + a posedge block. State constants are injected as arguments.

## Interface Bundles

Group related signals into reusable interfaces:

```python
class AXILite(Interface):
    def __init__(self, data_width=32, addr_width=32):
        self.awaddr  = ('input', addr_width)
        self.awvalid = ('input', 1)
        self.awready = ('output', 1)
        self.wdata   = ('input', data_width)
        self.wvalid  = ('input', 1)
        self.wready  = ('output', 1)

class Peripheral(Module):
    def __init__(self):
        self.clock = Input()
        self.bus   = AXILite()
        super().__init__()
```

Signals are flattened with the interface name as prefix in Verilog: `bus_awaddr`, `bus_awvalid`, etc.

## Formal Properties

Add assertions and coverage points that check every clock cycle:

```python
class Counter(Module):
    def __init__(self):
        self.clock = Input()
        self.reset = Input()
        self.count = Output(4)
        self.reg   = Register(4)
        super().__init__()

        @self.assert_always(self.clock)
        def count_bounded():
            return int(self.count) < 16

        @self.cover(self.clock)
        def count_reaches_five():
            return int(self.count) == 5
```

`@self.assert_always` raises `AssertionError` during simulation if the property ever fails. `@self.cover` tracks whether the condition was ever true (check with `counter.count_reaches_five.hit`).

## Timing Annotations and SDC Output

Co-locate timing constraints with your logic:

```python
class MyDesign(Module):
    def __init__(self):
        self.clk   = Input()
        self.reset = Input()
        self.d     = Input(8)
        self.q     = Output(8)
        super().__init__()

        self.create_clock(self.clk, period_ns=10)
        self.max_delay(self.d, self.q, ns=5)
        self.false_path(self.reset, self.q)
```

Generate SDC:

```python
print(design.to_sdc())
# create_clock -period 10 [get_ports clk]
# set_max_delay 5 -from [get_ports d] -to [get_ports q]
# set_false_path -from [get_ports reset] -to [get_ports q]
```

## Pipeline Transforms

Insert pipeline registers with explicit stage boundaries:

```python
class Pipe(Module):
    def __init__(self, width=16):
        self.clock  = Input()
        self.reset  = Input()
        self.a      = Input(width)
        self.b      = Input(width)
        self.out    = Output(width)
        super().__init__()

        pipe = self.pipeline(self.clock, self.reset, width=width)
        pipe.stage(lambda: int(self.a) + int(self.b))   # stage 0: add
        pipe.stage(lambda prev: prev * 2)                # stage 1: shift

        @self.comb
        def output():
            self.out = pipe.result
```

Each `.stage()` call creates a register boundary. Data propagates one stage per clock cycle. Stages chain: `pipe.stage(...).stage(...)`. Reset clears all pipeline registers to zero.

## Examples

See [`examples/`](examples/) for complete modules: counter, ALU, pipeline register, forwarding mux, hazard unit, instruction decoder, and a multi-module datapath.
