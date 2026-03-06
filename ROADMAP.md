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
