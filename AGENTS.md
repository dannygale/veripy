# AGENTS.md

## Project overview

VeriPy is a Python HDL framework. One Python source file produces both a behavioral simulation (runs natively in Python) and synthesizable Verilog output. The goal is to write hardware once and verify it through dual-path testing — the same test runs against both the Python sim and the generated Verilog (via iverilog).

A native C simulation backend (`csim`) compiles the IR to C for near-Verilator performance with much faster compile times. A Verilator co-simulation backend wraps Verilator-compiled models for Python-driven testing.

## Key directories

```
veripy/
├── veripy/           # Core framework
│   ├── signal.py     # Signal, _Expr, _SliceProxy, Mem types
│   ├── module.py     # Module base class, @module decorator, pipeline, FSM
│   ├── parameter.py  # Parameter, ParamExpr, clog2() for parametric widths
│   ├── decorator.py  # @module decorator implementation, assignment rewriting
│   ├── rewriter.py   # AST rewriter for signal assignment transformation
│   ├── context.py    # @comb, @always, @behavioral, pipeline, cover decorators
│   ├── lower.py      # AST lowering: Python → IR (width inference, register detection)
│   ├── ir.py         # Intermediate representation nodes
│   ├── flatten.py    # flatten_ir(), topo_sort_comb() — hierarchical IR → flat IR
│   ├── dce.py        # Dead code elimination and constant propagation
│   ├── backend_verilog.py  # IR → Verilog emission
│   ├── emit_verilog.py     # Verilog formatting helpers
│   ├── backend_csim.py     # IR → C emission, CSimModel ctypes wrapper, native TB
│   ├── backend_verilator.py # Verilator wrapper, native TB compilation
│   ├── backend_formal.py   # SymbiYosys .sby generation
│   ├── backend_equiv.py    # Yosys equivalence checking script generation
│   ├── backend_wgpu.py     # WebGPU batch-parallel simulation
│   ├── sim.py        # SimEngine, reactive yields (until), fork/join
│   ├── verify.py     # VeripyTestCase: multi-backend dual-path testing
│   ├── rand.py       # Constrained random (Rand, Range)
│   ├── driver.py     # Protocol driver base class
│   ├── blackbox.py   # BlackBox module for vendor IP instantiation
│   ├── config.py     # veripy.toml project configuration loader
│   ├── project.py    # Project: dependency-ordered builds, Tcl generation
│   ├── packaging.py  # IP package discovery and scaffolding
│   ├── autodoc.py    # Markdown documentation generation from modules
│   ├── csr.py        # Field, Reg, RegisterMap for CSR generation
│   ├── axi4lite.py   # AXI4-Lite bus and subordinate IP
│   ├── cdc.py        # Synchronizer, AsyncFIFO
│   ├── ip.py         # IP library (SyncFifo, arbiters, etc.)
│   ├── lint.py       # Static analysis checks
│   ├── import_verilog.py  # Verilog → VeriPy converter
│   ├── gpu_sim.py    # GPU simulation wrapper
│   ├── cli.py        # CLI entry point
│   └── __init__.py   # Public API exports
├── tests/            # Unit tests (unittest, dual-path)
├── examples/         # Example modules (counter, SPI controller, cpu_v1)
├── benchmarks/       # Performance benchmarks
│   ├── sim_vs_rtl.py    # All-backend comparison (Python, vvp, C-native, Verilator)
│   ├── stress_test.py   # SpiHub stress test (4× SPI + arbiter)
│   ├── cpu_bench.py     # RV32I CPU benchmark (4-way backend comparison)
│   ├── programs/        # C/asm programs for CPU benchmark
│   └── BASELINES.md     # Recorded benchmark baselines for tracking progress
├── docs/             # Documentation
│   ├── guide.md      # @module tutorial, signals, sub-modules, FSM, pipelines, formal
│   ├── class-api.md  # Module base class for advanced use
│   ├── signals.md    # Signal types, operations, slicing, memory arrays, parameters
│   ├── testing.md    # Multi-backend testing, SimEngine, VCD, reactive waits, drivers
│   ├── verilog.md    # Import, export, emission, lint
│   ├── equiv.md      # Formal equivalence via Yosys
│   └── gpu.md        # WGPU backend, batch-parallel verification
└── scripts/          # Automation (autopilot)
```

## How to build and test

```bash
# Run full test suite
python -m unittest discover -s tests

# Build Verilog from a module
veripy build <file.py>

# Lint
veripy lint <file.py>

# Run benchmarks
python benchmarks/sim_vs_rtl.py
python benchmarks/stress_test.py
```

All 726+ tests must pass before committing.

## C simulation backend (csim)

The csim backend in `backend_csim.py` compiles flat IR to C:

- `emit_c(ir)` — generates a C source file with `State` struct, comb/seq block functions, `veripy_eval()`, and per-signal `veripy_set_*/veripy_get_*` API
- `emit_tb_c(tb_ir, model_c_src, model_ir=)` — lowers testbench IR to C, produces a standalone `run_bench()` entry point
- `compile_bench(module, tb_ir, name)` — end-to-end: lower → flatten → topo_sort → emit_c → emit_tb_c → cc -O2 → ctypes load
- `CSimModel` — ctypes wrapper for driving compiled models from Python

Key optimizations implemented:
- Signal packing: 1-bit signals packed into `uint64_t` bitfields
- Dead code elimination on flat IR
- Per-block function splitting with `always_inline` for trivial blocks
- Cache-aware topological sort
- NBA (non-blocking assignment) temporaries with per-edge-group snapshot/commit
- Selective comb re-settle: only re-evaluates comb blocks that read seq-written signals
- Trivial comb block inlining into eval body
- Direct struct access in native testbench (no function call overhead)
- Aliased clock domain merging (single edge block for same-clock domains)
- Change-driven comb re-evaluation via per-signal dirty flags
- Continuous assign inlining into dependent comb blocks
- Profile-guided optimization (PGO) two-pass compile
- Batched packed-bit writes to same word
- Hierarchical per-module compilation (`emit_c_hier`, `csim_hier` backend)
- VCD waveform tracing with runtime enable toggle
- Memory load/store API for Mem arrays
- Runtime assertion checking in compiled models
- Internal signal exposure (regs, mems) via `CSimModel.get()`

See `benchmarks/BASELINES.md` for current performance numbers.

## Project configuration

`config.py` loads `veripy.toml` project files. The CLI (`cli.py`) reads `veripy.toml` when present to set defaults for build output, test paths, module selection, and FPGA targeting. `veripy init` scaffolds a new project with a starter `veripy.toml`.

## Conventions

- **Signal types**: `Input`, `Output`, `Register`, `Signal` are the wire types. `_Expr` is a lazy expression from operators — not instantiated directly.
- **Width inference**: The lowerer infers widths from RHS expressions in @comb blocks. Only declare `Register(width)` for locals when the RHS references other locals.
- **Pipelines**: Lambda-chain for simple cases, `pipe.stage('name', stall, flush, field=source)` for CPU-style pipelines.
- **Testing**: `VeripyTestCase` runs each test against up to 5 backends (Python sim, iverilog, csim flat, csim hierarchical, and optionally Verilator). Don't add tests unless the work requires them.
  - Subclass `VeripyTestCase`, implement `create_module()` to return your module instance.
  - Each `test_*` method runs automatically against all enabled backends — outputs are compared cycle-by-cycle.
  - Use `@self.always` for clocks, `@self.initial` for stimulus. `self.set()` drives inputs, `self.out()` reads outputs.
  - Reactive helpers: `yield until(lambda: cond)`, `self.fork()`, `self.fork_any()` for parallel blocks.
  - See [docs/testing.md](docs/testing.md) for full details.
- **Minimal code**: Follow existing patterns. Don't over-abstract. Explicit wiring over magic.
- **No separate Wire type**: `Signal` is the wire type.

## Project management

This project uses `pm` (CLI tool) to track features and tasks:

```bash
pm feature list          # see planned/in-progress/implemented features
pm todo list             # see open/in-progress tasks
pm feature show <id>     # details + dependencies
pm todo show <id>        # details + blocked-by
```

Check `pm` status before starting work. Update it when done. See `pm autopilot --help` for the automated workflow.

## Branch strategy

- `develop` — main development branch
- `feature/<name>` — for non-trivial features
- `fix/<name>` — for bug fixes
- Merge back to develop when tests pass

## Do not

- Modify existing tests unless changing the code they test
- Add unnecessary abstractions or helpers
- Use `connect_stage()` auto-wiring (rejected as too magical)
- Add forwarding helpers (rejected as too CPU-specific)
- Hardcode secrets or keys in code
