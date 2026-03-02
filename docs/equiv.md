# Equivalence Checking

Formally prove that two Verilog modules produce identical outputs for all possible inputs, using Yosys `equiv_check`.

## When to use

Dual-path testing (simulation) checks specific input sequences. Equivalence checking is exhaustive — it proves the modules are identical for *every* possible input, across *all* time steps. Use it when:

- Refactoring a module and want to guarantee identical behavior
- Comparing a hand-optimized gate-level design against the original
- Verifying that parameter changes don't alter functionality

## CLI

```
veripy equiv gold.py gate.py                    # generate Yosys script + Verilog files
veripy equiv gold.py gate.py -o out/            # write to output directory
veripy equiv gold.py gate.py --run              # run Yosys automatically (requires yosys on PATH)
veripy equiv gold.py gate.py --gold-module Foo  # select specific module from file
veripy equiv gold.py gate.py --gold-param n=4   # pass parameters
```

This writes three files:

| File | Contents |
|---|---|
| `gold.v` | Verilog for the reference module |
| `gate.v` | Verilog for the implementation module |
| `equiv.ys` | Yosys script that runs `equiv_make` / `equiv_simple` / `equiv_induct` |

To run manually:

```
yosys -s equiv.ys
```

## From Python

```python
from veripy.lower import lower_module
from veripy.backend_verilog import emit_verilog
from veripy.backend_equiv import emit_equiv_script

gold_ir = lower_module(gold_instance, 'gold')
gate_ir = lower_module(gate_instance, 'gate')

gold_v = emit_verilog(gold_ir)
gate_v = emit_verilog(gate_ir)
script = emit_equiv_script(gold_ir, gate_ir)
```

`emit_equiv_script` validates that both modules have matching port signatures (names, directions, widths) and raises `ValueError` on mismatch.

## How it works

1. Both modules are lowered to IR and emitted as Verilog
2. A Yosys script renames each module (`gold`, `gate`) and runs:
   - `equiv_make` — pairs corresponding signals
   - `equiv_simple` — proves equivalence via SAT for combinational logic
   - `equiv_induct` — proves equivalence via induction for sequential logic
   - `equiv_status -assert` — fails if any signal pair is unproven

## Requirements

- [Yosys](https://github.com/YosysHQ/yosys) on PATH (for `--run` or manual execution)
- Both modules must have identical port signatures
