# Guide — `@module` Tutorial

The `@module` decorator is the recommended way to define hardware modules in VeriPy. It eliminates `self.` boilerplate and reads like Verilog pseudocode.

## Basics

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
    def drive():
        count = cnt

    @always(posedge(clock))
    def increment():
        if reset:
            cnt = 0
        elif enable:
            cnt = cnt + 1
```

Function arguments become Verilog parameters. Signals are local variables — no `self.` needed. Assignments to signals inside `@comb` and `@always` blocks are automatically rewritten to drive the signal.

```python
from veripy.sim import SimEngine

c = counter(width=4)
sim = SimEngine(c)
sim.clock(c.clock, 10)

@sim.initial
def demo():
    c.reset.set(1); c.enable.set(1); yield 10
    c.reset.set(0)
    for _ in range(5):
        yield 10
    print(int(c.count))

sim.run()
print(c.to_verilog())
```

## How Assignment Rewriting Works

Inside `@comb` and `@always` blocks, VeriPy rewrites assignments to known signal names:

```python
count = cnt          # → count._assign(cnt)     (drives the signal)
cnt += 1             # → cnt._assign(cnt + 1)   (augmented assignment)
data[3:0] = 0xA      # → data[3:0]._assign(0xA) (bit slice write)
alu.a = x            # → alu.a._assign(x)       (sub-module port drive)
```

Regular Python variables are unaffected — only names declared as signals in the module body are rewritten. You can freely use local variables:

```python
@comb
def compute():
    temp = int(a) + int(b)    # normal local — 'temp' is not a signal
    result = temp * 2          # drives signal — 'result' IS a signal
```

## Parameters

Function arguments are Verilog parameters with defaults:

```python
@module
def alu(width=8):
    a   = Input(width)
    b   = Input(width)
    out = Output(width + 1)   # parameter arithmetic works
```

```python
a = alu(width=16)  # out is 17 bits wide
```

## Logic Blocks

### `@comb` — Combinational Logic

```python
@comb
def drive():
    out = a + b
```

Simple assignments emit `assign`. Control flow emits `always @(*)`.

### `@always(posedge(signal))` — Sequential Logic

```python
@always(posedge(clock))
def increment():
    if reset:
        cnt = 0
    else:
        cnt = cnt + 1
```

Emits `always @(posedge clock)` with non-blocking assignments (`<=`).

### `@always(negedge(signal))` — Falling Edge

```python
from veripy import negedge

@always(negedge(clock))
def capture():
    q = d
```

### `@always(sensitivity)` — Multi-Edge Sensitivity

```python
from veripy import posedge, negedge
from veripy.context import always

@always(posedge(clock) | negedge(rst_n))
def async_reset():
    if not rst_n:
        reg = 0
    else:
        reg = d
```

Emits `always @(posedge clock or negedge rst_n)`.

## Sub-Modules

Instantiate other modules as local variables. Drive their ports from `@comb` blocks:

```python
@module
def datapath(width=16):
    clock  = Input()
    a      = Input(width)
    b      = Input(width)
    result = Output(width)
    alu    = my_alu(width=width)

    @comb
    def wire():
        alu.a = a
        alu.b = b
        result = alu.out
```

Sub-modules with parameters emit `#(.param(value))` overrides in Verilog. Each module emits its own `.v` file via `to_verilog()`.

## FSM

Declarative state machines with `@fsm`:

```python
from veripy.context import fsm

@module
def traffic_light():
    clock = Input()
    reset = Input()
    go    = Input()
    color = Output(2)

    @fsm(clock, reset, states=['RED', 'GREEN', 'YELLOW'])
    def light(state, RED, GREEN, YELLOW):
        if state == RED:
            color = 0
            if go:
                return GREEN
        elif state == GREEN:
            color = 1
            return YELLOW
        elif state == YELLOW:
            color = 2
            return RED
```

The decorator creates a state register and next-state logic. State constants are injected as function arguments. Return a state name to transition.

## Formal Properties

```python
from veripy.context import assert_always, cover

@module
def safe_counter():
    clock = Input()
    count = Output(4)
    cnt   = Register(4)

    @always(posedge(clock))
    def inc():
        cnt = cnt + 1

    @comb
    def drive():
        count = cnt

    @assert_always(clock)
    def bounded():
        return int(count) < 16

    @cover(clock)
    def reaches_five():
        return int(count) == 5
```

`@assert_always` raises `AssertionError` during simulation if the property fails. `@cover` tracks whether the condition was ever true.

## Pipelines

### Lambda-Chain (Simple)

```python
from veripy.context import pipeline

@module
def pipe(width=16):
    clock = Input()
    reset = Input()
    a     = Input(width)
    b     = Input(width)
    out   = Output(width)

    pipe = pipeline(clock, reset, width=width)
    pipe.stage(lambda: int(a) + int(b))
    pipe.stage(lambda prev: prev * 2)

    @comb
    def output():
        out = pipe.result
```

Each `.stage()` creates a register boundary. Data propagates one stage per clock cycle. Reset clears all pipeline registers.

### Named Stages (Multi-Stage CPU Pipelines)

For real pipelines with per-stage stall/flush and many fields, use named stages:

```python
@module
def cpu(width=32):
    clk = Input()
    rst = Input()
    instr = Input(width)
    pc    = Input(width)
    dec_alu_op = Input(5)
    dec_rd     = Input(5)
    stall      = Input()
    flush      = Input()

    pipe = pipeline(clk, rst)

    # pipe.stage('name', stall, flush, field=source, ...)
    if_id = pipe.stage('if_id', stall, flush,
        instr=instr,
        pc=pc)

    id_ex = pipe.stage('id_ex', stall, None,
        alu_op=dec_alu_op,
        rd=dec_rd,
        pc=if_id.pc)           # chain from previous stage
```

Each `field=source` pair creates a register named `{stage}_{field}` (e.g. `if_id_instr`). Width is inferred from the source signal. On each posedge:

- **reset** → zero all fields
- **stall** → hold current values
- **flush** → zero all fields
- **else** → latch source values

Access fields via `stage.field` (e.g. `if_id.pc`, `id_ex.alu_op`).

#### Expression Sources

Signal expressions work directly as stall/flush conditions and field sources — no intermediate registers needed:

```python
_flush = hzu.flush_if_id & ~mem_stall
if_id = pipe.stage('if_id',
    pipeline_stall & ~_flush, _flush,       # expressions as stall/flush
    valid=(fsm == 2),                        # comparison expression as field source
    link_pc=id_ex.pc + 4,                    # arithmetic expression
    byte_off=alu.result[1:0])                # bit slice as source
```

Expressions emit inline in the generated Verilog (e.g. `link_pc <= (id_ex_pc + 4)`).

## Timing Constraints

```python
from veripy.context import create_clock, max_delay, false_path

@module
def design():
    clk   = Input()
    reset = Input()
    d     = Input(8)
    q     = Output(8)

    create_clock(clk, period_ns=10)
    max_delay(d, q, ns=5)
    false_path(reset, q)
```

Generate SDC with `design().to_sdc()`.

## Interfaces

```python
from veripy.signal import Interface

class AXILite(Interface):
    awaddr  = ('input', 32)
    awvalid = ('input', 1)
    awready = ('output', 1)

@module
def peripheral():
    clock = Input()
    bus   = AXILite()

    @comb
    def logic():
        bus.awready = 1
```

Signals are flattened with the interface name as prefix in Verilog: `bus_awaddr`, `bus_awvalid`, etc.

### Parameterized Interfaces

Override `__init__` to make widths configurable:

```python
class AXILite(Interface):
    def __init__(self, data_width=32, addr_width=32):
        self.awaddr  = ('input', addr_width)
        self.awvalid = ('input', 1)
        self.awready = ('output', 1)
        self.wdata   = ('input', data_width)
        self.wready  = ('output', 1)
        super().__init__()

@module
def peripheral(data_width=32):
    bus = AXILite(data_width=data_width)
```

Set signal tuples as instance attributes before calling `super().__init__()`.

### 3-Level Attribute Resolution

Sub-module interface signals resolve through three levels in comb blocks:

```python
@module
def top():
    sub_a = SubModuleA()
    sub_b = SubModuleB()

    @comb
    def wire():
        sub_a.bus.wdata = sub_b.bus.rdata  # emits: sub_a_bus_wdata = sub_b_bus_rdata
```

`self.sub.iface.signal` resolves to `sub_iface_signal` in both target and expression positions.

### Bulk Interface Connect

Assign one interface to another to wire all matching signals automatically:

```python
@comb
def connect():
    sub_a.bus = sub_b.bus  # expands to per-signal assigns
```

Matching is by signal name. Direction is respected: outputs on the source wire to inputs on the destination. This replaces tedious per-signal wiring when two sub-modules share the same interface type.


## CDC

Cross clock domain signals must be synchronized. VeriPy provides two primitives and lint detection.

### CDC Lint

The lint pass automatically detects unsynchronized register reads across clock domains:

```python
from veripy.lint import lint

warnings = lint(my_design)
# warning: CDC: register 'reg_fast' (clocked by fast_clk) read in block clocked by slow_clk
```

No annotations needed — domains are inferred from `@always(posedge(...))` blocks.

### Synchronizer

Multi-stage flip-flop chain for single-bit or bus synchronization:

```python
from veripy import Synchronizer

@module
def design():
    fast_clk = Input()
    slow_clk = Input()
    rst      = Input()
    d        = Input(8)
    q        = Output(8)
    reg_fast = Register(8)
    sync     = Synchronizer(width=8, stages=2)

    @always(posedge(fast_clk))
    def fast():
        reg_fast = d

    @comb
    def wire():
        sync.clk = slow_clk
        sync.rst = rst
        sync.d   = reg_fast

    @comb
    def out():
        q = sync.q
```

Data propagates through the synchronizer in N clock cycles (one per stage). Using a `Synchronizer` suppresses the CDC lint warning for that path.

### AsyncFIFO

Gray-code pointer FIFO for bulk data transfer across clock domains:

```python
from veripy import AsyncFIFO

fifo = AsyncFIFO(width=8, depth=16)  # depth must be power of 2
# Write side: fifo.wclk, fifo.wrst, fifo.wen, fifo.wdata, fifo.full
# Read side:  fifo.rclk, fifo.rrst, fifo.ren, fifo.rdata, fifo.empty
```

Gray-code pointers ensure only one bit changes per clock cycle during pointer synchronization, preventing metastability-induced corruption.
