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

```python
from veripy.signal import Mem

regs = Mem(depth=4, width=8)

val = regs[addr]              # combinational read
regs.write(addr, data)        # scheduled write (applied on tick)
```

In `@always(posedge(...))` blocks, `mem.write(addr, data)` emits `mem[addr] <= data`.

## Non-Blocking Assignment

Signals use non-blocking assignment internally:

```python
sig._assign(value)   # schedule next-cycle update
sig._tick()          # apply pending update
```

This is handled automatically by `@comb`/`@always` blocks and `SimEngine`. You rarely need to call these directly.
