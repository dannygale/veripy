# AGENTS.md

## Project overview

VeriPy is a Python HDL framework. One Python source file produces both a behavioral simulation (runs natively in Python) and synthesizable Verilog output. The goal is to write hardware once and verify it through dual-path testing — the same test runs against both the Python sim and the generated Verilog (via iverilog).

## Key directories

```
veripy/
├── veripy/           # Core framework
│   ├── signal.py     # Signal, _Expr, _SliceProxy, Mem types
│   ├── module.py     # Module base class, @module decorator, pipeline, FSM
│   ├── lower.py      # AST lowering: Python → IR (width inference, register detection)
│   ├── ir.py         # Intermediate representation nodes
│   ├── backend_verilog.py  # IR → Verilog emission
│   ├── sim.py        # SimEngine, reactive yields (until), fork/join
│   ├── rand.py       # Constrained random (Rand, Range)
│   ├── context.py    # @comb, @always, pipeline, cover decorators
│   └── __init__.py   # Public API exports
├── tests/            # Unit tests (unittest, dual-path)
├── examples/         # Example modules (counter, SPI controller)
├── docs/             # Documentation (guide, signals, testing, verilog, class-api)
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
```

All 358+ tests must pass before committing.

## Conventions

- **Signal types**: `Input`, `Output`, `Register`, `Signal` are the wire types. `_Expr` is a lazy expression from operators — not instantiated directly.
- **Width inference**: The lowerer infers widths from RHS expressions in @comb blocks. Only declare `Register(width)` for locals when the RHS references other locals.
- **Pipelines**: Lambda-chain for simple cases, `pipe.stage('name', stall, flush, field=source)` for CPU-style pipelines.
- **Testing**: `VeripyTestCase` runs each test twice (Python sim + iverilog). Don't add tests unless the work requires them.
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
