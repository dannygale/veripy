# VeriPy Roadmap

## Simulation performance — closing the Verilator gap

Current state (2026-03-05): csim is within 16-20% of Verilator execution speed on the SpiHub stress test, with 11-21× faster total turnaround thanks to sub-second compile times. See `benchmarks/BASELINES.md` for numbers.

### Implemented

- Native C backend (`backend_csim.py`): IR → C → shared lib
- Signal packing: 1-bit signals into `uint64_t` bitfields
- Dead code elimination on flat IR
- Per-block function splitting with trivial block inlining
- Cache-aware topological sort
- NBA temporaries with per-edge-group snapshot/commit
- Selective comb re-settle (only blocks that read seq-written signals)
- Direct struct access in native testbench
- Verilator native testbench for fair benchmarking

### Planned optimizations

| Feature | Priority | Expected impact |
|---------|----------|-----------------|
| `csim-cflags` — -O3 -march=native -flto | high | 5-10% free |
| `csim-clock-merge` — aliased clock domain merging | critical | Large for multi-instance designs (SpiHub has 9 edge blocks on same clock) |
| `csim-change-driven` — dirty-flag comb re-evaluation | high | Big win when most signals are stable per cycle |
| `csim-cont-inline` — inline trivial continuous assigns | high | _cont_assigns was 23% of profiling time |
| `csim-pack-batch` — batch writes to same pack word | medium | Reduces read-modify-write overhead |
| `csim-pgo` — profile-guided optimization | medium | 10-15% on branch-heavy FSM code, zero code changes |

### Real-world benchmark

`csim-cpu-bench` — use the RV32I pipelined CPU from `../vhdl_cpu/isa/v3` (pipeline + multi-level cache + AXI interconnect + memory, ~15 submodules, ~2500 lines) as a large-scale benchmark. Blocked on a lowering bug in `trap_unit` (#81).

## Event-driven hybrid simulation

The current csim backend is a pure compiled cycle-accurate simulator: evaluation order is baked in at compile time via topological sort, and `veripy_eval()` runs all logic in a fixed sequence every clock edge. This is the fastest approach for functional verification but has no visibility into intra-cycle timing.

An event-driven hybrid mode would keep the compiled eval kernels (`_comb_N`, `_seq_N`) but replace the fixed topo-ordered dispatch with a lightweight event scheduler. This is the same architecture used by commercial simulators like Synopsys VCS — compiled blocks for speed, event queue for timing fidelity.

### What it enables

- Delta-cycle visibility: see combinational settling within a time step
- `#delay` scheduling: proper Verilog delay semantics in simulation
- Mixed-clock-domain verification without cycle-accurate assumptions
- Foundation for future SDF back-annotation (post-layout timing)

### Architecture

```
┌─────────────────────────────────────────────┐
│  Event scheduler (priority queue on time,δ) │
│                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ _comb_0  │ │ _seq_1   │ │ _comb_3  │    │  ← same compiled blocks
│  └──────────┘ └──────────┘ └──────────┘    │    as cycle-accurate mode
│                                             │
│  sensitivity lists + change detection       │
│  signal changes → enqueue dependent blocks  │
└─────────────────────────────────────────────┘
```

### Implementation plan

1. C event scheduler: priority queue keyed on `(time, delta)`, ~200 lines
2. Sensitivity-driven dispatch: block runs → diff outputs → enqueue dependents
3. Change detection: compare written signals to previous values (write sets already known from `nba_per_seq` and `comb_deps`)
4. `#delay` support: TB `Delay(N)` nodes emit `schedule(time + N, callback)` instead of `_step()` loops
5. New emitter `emit_c_event()`: same block functions, different dispatch wrapper
6. Delta-cycle convergence: iterate at current time until no pending events, with loop detection

### Expected cost

3-5× slower than the compiled path. Still 20-50× faster than vvp. Selectable via flag: `compile_bench(..., mode='event')`.

### Priority

Low — cycle-accurate covers most verification needs. Build when mixed-clock or timing-aware simulation is required.

## Multi-threaded simulation

Partition the compiled eval across multiple cores. The flat IR dependency graph already identifies independent subgraphs — clock domains, submodule trees, and unconnected comb chains can run in parallel. The scheduler would fork independent partitions, barrier-sync at clock edges, then commit.

Probably only matters for large designs (1000+ signals) where single-core eval is the bottleneck. Verilator supports this with `--threads` and reports 2-3× speedup on big designs with good partitioning.

### Priority

Low — focus on single-core optimizations first. Revisit when the CPU benchmark is running and single-core is proven to be the bottleneck.

## Waveform dumping from csim

The compiled model currently runs blind — no signal trace output. Adding opt-in waveform dumping would make csim usable for debugging, not just benchmarking.

### Plan

- Instrument `veripy_eval()` with a post-eval hook that records changed signals
- Support FST format (compact, used by GTKWave) — write via `fstapi.h` or a minimal C implementation
- VCD as a fallback (simple but large)
- Signal selection: dump all, or a user-specified subset to minimize overhead
- Flag: `compile_bench(..., trace='signals.fst')` or `CSimModel(trace=True)`

### Priority

High — this is the main thing preventing csim from replacing vvp in day-to-day debugging workflows.

## Co-simulation with external C/SystemVerilog

DPI-like interface so csim-compiled models can call into user C code or be driven from SystemVerilog testbenches. This is how VeriPy models integrate into existing verification environments.

### Plan

- Export a stable C API: `veripy_create/destroy/eval/set_*/get_*` (already exists)
- Add callback hooks: `veripy_register_callback(signal, fn)` for monitor-style integration
- Generate a SystemVerilog wrapper with DPI imports that maps to the C API
- Support linking user `.c` files into `compile_bench()`

### Priority

Medium — needed when VeriPy models are used as components in larger verification environments.

## Coverage instrumentation in csim

Line, toggle, and FSM state coverage collected during compiled simulation. The Python sim already has coverage reporting — extending it to csim makes the fast path usable for coverage-driven verification and signoff.

### Plan

- Toggle coverage: instrument each signal write with a bitmask OR tracking which bits have toggled 0→1 and 1→0
- Line coverage: counter per comb/seq block, increment on entry
- FSM coverage: track visited states and transitions per FSM register
- Emit coverage data to a shared struct, read back via ctypes after simulation
- Merge with existing Python coverage reporting infrastructure

### Priority

Medium — important for verification signoff but not blocking current development.

## GPU-accelerated batch simulation

Port the compiled eval to a GPU compute shader for massively parallel parameter/seed sweeps. Not about making one simulation faster — it's about running thousands of variants simultaneously.

The WGPU batch-parallel backend already exists for the Python sim (`docs/gpu.md`). Extending it to csim means generating WGSL/SPIR-V from the same flat IR instead of C.

### Plan

- New emitter `emit_wgsl(ir)` targeting WebGPU compute shaders
- Each workgroup instance gets its own `State` struct in shared memory
- Testbench parameters (seed, config) vary per instance
- Collect pass/fail + coverage per instance, reduce on readback
- Reuse existing `wgpu` infrastructure from the Python GPU backend

### Priority

Low — the Python GPU backend covers this use case today. Revisit when csim performance on single instances is fully optimized and batch throughput becomes the bottleneck.

## SystemVerilog assertion support

Compile SVA-like temporal assertions and cover sequences into the eval loop. The formal infrastructure (`assert_always`, `cover`) already handles single-cycle properties — temporal sequences (`##1`, `|->`, `[*N]`) would be the next step.

### Plan

- New IR nodes for sequence expressions: delay, repetition, implication
- Python API: `assert_property(a |-> ##1 b)` or decorator syntax
- Compile to state machines in C: each assertion becomes a small FSM tracked in the State struct
- Report failures with cycle number and signal values
- Integrate with formal backend (emit SVA for SymbiYosys)

### Priority

Medium — valuable for complex protocol verification. The single-cycle `assert_always` covers most current use cases.

## Incremental compilation

Cache compiled `.so` files and only recompile when the design changes. The current flow recompiles everything on every run — even though compile time is already fast (~0.2s), dropping it to near-zero for unchanged designs improves the edit-simulate loop.

### Plan

- Hash the generated C source (or the flat IR) and use as cache key
- Store compiled `.so` in a cache directory (`.veripy_cache/`)
- On `compile_bench()`, check cache before invoking `cc`
- Invalidate on: source hash mismatch, compiler flag change, veripy version bump
- Optional: per-block incremental recompile (only recompile changed `_comb_N`/`_seq_N` functions and relink)

### Priority

Medium — nice quality-of-life improvement. Most impactful when iterating on testbenches with an unchanged design.
