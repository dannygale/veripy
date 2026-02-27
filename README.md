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
c.enable._val = 1
c.reset._val = 1
c.tick()
c.reset._val = 0
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

## CLI

```
veripy build <file.py>                  # emit Verilog to stdout (compile-checked via iverilog)
veripy build <file.py> -o out/          # write .v files to a directory
veripy build <file.py> -m Counter       # target a specific Module subclass
veripy build <file.py> -p n=4           # pass parameters
veripy test [path] [-v]                 # run dual-path test suite
```

`build` always compiles the emitted Verilog through iverilog before outputting. If the generated RTL is broken, you'll see the errors immediately.

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
| `Register(width)` | Clocked register (updates on posedge) |
| `Signal(width)` | Internal wire |
| `Mem(width, depth)` | Memory array with `.write(addr, data)` |

## Module Structure

Modules define signals in `__init__`, then register logic blocks:

- `@self.comb` — combinational logic (emits `assign` or `always @(*)`)
- `@self.posedge(self.clock)` — sequential logic (emits `always @(posedge clk)`)

Sub-modules are declared as attributes and automatically discovered for Verilog emission.

## Examples

See [`examples/`](examples/) for complete modules: counter, ALU, pipeline register, forwarding mux, hazard unit, instruction decoder, and a multi-module datapath.
