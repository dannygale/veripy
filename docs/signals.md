# Signal Types

## Overview

| Type | Description | Verilog |
|------|-------------|---------|
| `Input(width)` | Input port (default width 1) | `input [w-1:0] name` |
| `Output(width)` | Output port | `output [w-1:0] name` |
| `Register(width, reset=0)` | Stateful register | `reg [w-1:0] name` |
| `Signal(width)` | Internal wire | `wire [w-1:0] name` |
| `Mem(depth, width)` | Memory array | `reg [w-1:0] name [0:d-1]` |

## Reading and Setting Values

```python
sig = Output(8)
sig.set(42)          # immediate set (testbenches)
print(int(sig))      # 42
print(sig.val)       # 42
print(bool(sig))     # True (non-zero)
```

## Operators

All arithmetic and bitwise operators return lazy `_Expr` objects that re-evaluate on each `int()` / `bool()` call, always reflecting current signal values:

```python
a = Signal(8); a.set(10)
b = Signal(8); b.set(3)

expr = a + b     # _Expr — not yet evaluated
int(expr)        # 13 — evaluates now
a.set(20)
int(expr)        # 23 — re-evaluates with current values
```

Expressions carry width metadata, so they can be used directly as pipeline stage sources without intermediate registers.

Arithmetic (width-masked):

```python
a + b    # 13
a - b    # 7
a & b    # 2
a | b    # 11
a ^ b    # 9
~a       # 245 (8-bit invert)
a << 2   # 40
a >> 1   # 5
```

Comparisons (return 1-bit `_Expr`):

```python
a == b   # _Expr → 0
a > b    # _Expr → 1
a <= 10  # _Expr → 1
```

Expressions compose — `(a + b) & mask` builds a chain of `_Expr` objects. Using `int()`, `bool()`, or `if expr:` evaluates immediately.

## Bit Slicing

```python
data = Signal(8)
data.set(0xAB)

data[7:4]        # SliceProxy → 0xA
data[3:0]        # SliceProxy → 0xB
data[0]          # single bit → 1

data[7:4] = 0xF  # data becomes 0xFB
```

## Concatenation

Assign a list to concatenate signals (MSB first):

```python
@comb
def concat():
    out = [high, low]    # Verilog: assign out = {high, low}
```

## Ternary Mux

```python
@comb
def select():
    out = a if sel else b    # Verilog: assign out = sel ? a : b
```

## Memory Arrays

### Mem

Basic memory array with combinational read and synchronous write.

```python
from veripy.signal import Mem

regs = Mem(depth=4, width=8)

val = regs[addr]              # combinational read
regs.write(addr, data)        # scheduled write (applied on tick)
```

In `@always(posedge(...))` blocks, `mem.write(addr, data)` emits `mem[addr] <= data`.

### DualPortMem

Simple dual-port memory: 1 write port + 1 read port with synchronous read. All port signals are passed as keyword arguments.

```python
from veripy import DualPortMem

ram = DualPortMem(depth, width, style=None,
                  clock=clock, we=we,
                  waddr=waddr, wdata=wdata,
                  raddr=raddr, rdata=rdata)
```

Generates the synthesis-friendly pattern:

```verilog
always @(posedge clock) begin
    if (we) ram[waddr] <= wdata;
    rdata <= ram[raddr];
end
```

`rdata` must be an `Output` — it becomes `output reg` in Verilog since the read is synchronous.

```python
class MyRam(Module):
    def __init__(self, depth=16, width=8):
        self.clock = Input()
        self.we    = Input()
        self.waddr = Input(4)
        self.wdata = Input(width)
        self.raddr = Input(4)
        self.rdata = Output(width)
        self.ram   = DualPortMem(depth, width,
                                 clock=self.clock, we=self.we,
                                 waddr=self.waddr, wdata=self.wdata,
                                 raddr=self.raddr, rdata=self.rdata)
        super().__init__()
```

### TrueDualPortMem

True dual-port memory: 2 independent read/write ports with synchronous reads. Each port has its own clock, write enable, address, data in, and data out.

```python
from veripy import TrueDualPortMem

ram = TrueDualPortMem(depth, width, style=None,
                      clka=clka, wea=wea, addra=addra, dina=dina, douta=douta,
                      clkb=clkb, web=web, addrb=addrb, dinb=dinb, doutb=doutb)
```

Generates two independent always blocks:

```verilog
always @(posedge clka) begin
    if (wea) ram[addra] <= dina;
    douta <= ram[addra];
end
always @(posedge clkb) begin
    if (web) ram[addrb] <= dinb;
    doutb <= ram[addrb];
end
```

Both `douta` and `doutb` must be `Output` signals (emitted as `output reg`).

### ram_style

All three memory types (`Mem`, `DualPortMem`, `TrueDualPortMem`) accept an optional `style` parameter that controls FPGA synthesis tool memory inference:

```python
Mem(16, 8, style='block')
DualPortMem(16, 8, style='distributed', clock=clk, ...)
TrueDualPortMem(16, 8, style='ultra', clka=clka, ...)
```

| Style | Verilog Attribute | Target |
|-------|-------------------|--------|
| `None` | (none) | Tool decides |
| `'block'` | `(* ram_style = "block" *)` | Block RAM (BRAM) |
| `'distributed'` | `(* ram_style = "distributed" *)` | LUT RAM / distributed RAM |
| `'ultra'` | `(* ram_style = "ultra" *)` | UltraRAM (Xilinx UltraScale+) |

The attribute is emitted on the memory declaration:

```verilog
(* ram_style = "block" *) reg [7:0] ram [0:15];
```

Invalid style values raise `ValueError` at construction time.

## Non-Blocking Assignment

Signals use non-blocking assignment internally:

```python
sig._assign(value)   # schedule next-cycle update
sig._tick()          # apply pending update
```

This is handled automatically by `@comb`/`@always` blocks and `SimEngine`. You rarely need to call these directly.

## Parameters and Parametric Widths

### `Parameter`

`Parameter` is a deferred width placeholder for class-based modules. It resolves to a Verilog parameter at emission time:

```python
from veripy import Parameter

class MyModule(Module):
    def __init__(self):
        self.width = Parameter(8)
        self.data  = Input(self.width)
        self.out   = Output(self.width)
        super().__init__()
```

### Arithmetic on Parameters

Parameters support `+`, `-`, `*`, `//`. These return `ParamExpr` objects that resolve at elaboration:

```python
self.out = Output(self.width + 1)       # width+1 bits
self.addr = Input(self.depth // 2)
```

### Comparison Operators

Parameters support `>`, `<`, `>=`, `<=`, `==`, `!=`. These evaluate against the default value during Python simulation:

```python
if self.width > 16:
    self.overflow = Output(1)
```

### `clog2()`

Compute `$clog2()` of a parameter for address widths:

```python
from veripy.parameter import clog2

class Fifo(Module):
    def __init__(self):
        self.depth = Parameter(16)
        self.width = Parameter(8)
        self.addr  = Input(clog2(self.depth))  # 4 bits for depth=16
        super().__init__()
```

Emits `$clog2(depth)` in Verilog. Works with `Parameter`, `ParamExpr`, and plain integers.
