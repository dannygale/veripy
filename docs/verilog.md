# Verilog

## Generating Verilog

```python
c = counter(width=4)
print(c.to_verilog())                      # single module

# For hierarchical designs, each sub-module emits independently:
d = datapath(width=8)
print(d.alu.to_verilog())                  # sub-module
print(d.to_verilog())                      # parent
```

CLI:

```
veripy build counter.py                     # emit to stdout (compile-checked via iverilog)
veripy build counter.py -o out/             # write .v files to directory
veripy build counter.py -p width=4          # pass parameters
veripy build counter.py -m Counter          # target specific module
```

`build` always compiles through iverilog before outputting — broken RTL is caught immediately.

## Other CLI Commands

```
veripy equiv gold.py gate.py               # formal equivalence checking (Yosys)
veripy equiv gold.py gate.py --run         # run Yosys automatically
veripy profile tests/test_counter.py       # compare Python sim vs iverilog performance
veripy doc counter.py                      # generate markdown documentation
veripy doc counter.py -o docs/             # write .md files to directory
veripy ip list                             # list installed VeriPy IP packages
veripy ip show <name>                      # show IP package details
veripy ip init <name>                      # scaffold a new IP package
veripy init                                # scaffold a new VeriPy project with veripy.toml
```

## Importing Verilog

Convert existing RTL to VeriPy:

```
veripy import alu.v                         # print Python to stdout
veripy import alu.v -o alu.py               # write to file
veripy import rtl/ -o src/                  # convert entire project
```

The importer handles: module declarations with parameters, parametric widths (`[width-1:0]` → `Input(width)`), `assign`, `always @(*)`, `always @(posedge ...)`, `if`/`else`, `case`, sub-module instances with parameter overrides, and cross-file module references.

From Python:

```python
from veripy.import_verilog import import_verilog, import_project

python_code = import_verilog('design.v')
files = import_project('rtl/')    # {relative_path: python_source}
```

## Lint

Static checks for common RTL mistakes:

```
veripy lint counter.py
```

Detects:
- Undriven outputs
- Multi-driven signals
- Missing reset on sequential blocks
- Unused signals
- Clock domain crossing violations (unsynchronized register reads across `@always(posedge(...))` blocks on different clocks)
- Combinational loops

From Python:

```python
from veripy.lint import lint
for warning in lint(counter(width=4)):
    print(warning)
```
