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
│   ├── lower.py      # AST lowering: Python → IR (width inference, register detection)
│   ├── ir.py         # Intermediate representation nodes
│   ├── flatten.py    # flatten_ir(), topo_sort_comb() — hierarchical IR → flat IR
│   ├── backend_verilog.py  # IR → Verilog emission
│   ├── backend_csim.py     # IR → C emission, CSimModel ctypes wrapper, native TB
│   ├── backend_verilator.py # Verilator wrapper, native TB compilation
│   ├── sim.py        # SimEngine, reactive yields (until), fork/join
│   ├── rand.py       # Constrained random (Rand, Range)
│   ├── context.py    # @comb, @always, pipeline, cover decorators
│   └── __init__.py   # Public API exports
├── tests/            # Unit tests (unittest, dual-path)
├── examples/         # Example modules (counter, SPI controller)
├── benchmarks/       # Performance benchmarks
│   ├── sim_vs_rtl.py    # All-backend comparison (Python, vvp, C-native, Verilator)
│   ├── stress_test.py   # SpiHub stress test (4× SPI + arbiter)
│   └── BASELINES.md     # Recorded benchmark baselines for tracking progress
├── docs/             # Documentation
│   ├── guide.md      # @module tutorial, signals, sub-modules, FSM, pipelines, formal
│   ├── class-api.md  # Module base class for advanced use
│   ├── signals.md    # Signal types, operations, slicing, memory arrays
│   ├── testing.md    # Dual-path testing, SimEngine, VCD, reactive waits, drivers
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

All 358+ tests must pass before committing.

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

See `benchmarks/BASELINES.md` for current performance numbers.

## Conventions

- **Signal types**: `Input`, `Output`, `Register`, `Signal` are the wire types. `_Expr` is a lazy expression from operators — not instantiated directly.
- **Width inference**: The lowerer infers widths from RHS expressions in @comb blocks. Only declare `Register(width)` for locals when the RHS references other locals.
- **Pipelines**: Lambda-chain for simple cases, `pipe.stage('name', stall, flush, field=source)` for CPU-style pipelines.
- **Testing**: `VeripyTestCase` runs each test twice (Python sim + iverilog). Don't add tests unless the work requires them.
  - Subclass `VeripyTestCase`, implement `create_module()` to return your module instance.
  - Each `test_*` method runs automatically in both Python simulation and compiled Verilog — outputs are compared cycle-by-cycle.
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
