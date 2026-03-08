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
| `csim-pgo` — profile-guided optimization | medium | 10-15% on branch-heavy FSM code; depends on model/TB split for caching |

### Real-world benchmark

`csim-cpu-bench` — use the RV32I pipelined CPU from `../vhdl_cpu/isa/v3` (pipeline + multi-level cache + AXI interconnect + memory, ~15 submodules, ~2500 lines) as a large-scale benchmark. Blocked on a lowering bug in `trap_unit` (#81).

## Model/testbench split compilation

Currently `compile_bench()` concatenates model C and testbench C into one file and recompiles everything on every run. The model should be compiled once into a cached `.so`/`.a`, with testbenches compiled separately and linked against it.

This is the same architecture Verilator uses: `verilator --cc` produces a model library, testbenches link against it. The model is the stable artifact; testbenches are what you iterate on.

### Why this matters

- Testbench iteration becomes near-instant (link only, no model recompile)
- Enables PGO: profile the model once, reuse across all testbenches
- Enables incremental compilation: cache the model `.so` by IR hash
- Correct separation of concerns: model is design-dependent, testbench is test-dependent
- Foundation for co-simulation: the model library is the reusable artifact external tools link against

### Design decisions

- Cache location: project-local build output directory (alongside other artifacts)
- `compile_model()` returns a `CompiledModel` object wrapping the `.so` path + metadata (IR hash, signal list, compiler flags). Not a full `CSimModel` — just enough for `compile_tb()` to validate compatibility and link against.
- PGO flow: compile model with `-fprofile-generate` → short warmup run (not full sim) → recompile with `-fprofile-use` → cache. Only pay PGO cost once per design change.

### Plan

1. `compile_model(module, name)` → compiles `emit_c()` output to `.so` with exported `veripy_create/destroy/eval/set_*/get_*` API, returns `CompiledModel`
2. `compile_tb(tb_ir, compiled_model, name)` → compiles `emit_tb_c()` output, links against model `.so`
3. Cache model `.so` in project build directory keyed on IR hash + compiler flags
4. `compile_bench()` becomes: check cache → compile model if needed → compile TB → link
5. PGO: compile model with `-fprofile-generate` → short warmup → recompile with `-fprofile-use` → cache

### Priority

Critical — this is the foundation that PGO, incremental compilation, and co-simulation all depend on.

## Incremental compilation

Per-block object files: each `_comb_N`/`_seq_N` function compiles to a separate `.o`. Change one block → recompile one `.o` → relink. Also enables parallel compilation (`cc -j`).

This is the same approach Verilator uses with `obj_dir/` — each block is a separate `.cpp` file compiled independently.

### Why per-block granularity

- Incremental recompile: change one comb block, rebuild one `.o`, relink in milliseconds
- Parallel compilation: all `.o` files can compile simultaneously (`make -j`)
- Foundation for multi-threaded simulation: per-block `.o` files map naturally to per-thread partitions

### Plan

1. `emit_c()` generates one `.c` file per block + a top-level `eval.c` with `veripy_eval()` and the `State` struct
2. Compile each block `.c` to `.o` independently (parallelizable)
3. Link all `.o` into model `.so`
4. Cache individual `.o` files by block content hash — only recompile changed blocks
5. Invalidate on: block source hash mismatch, compiler flags, struct layout change

### Priority

Medium — nice quality-of-life improvement. Most impactful on large designs where compile time grows. Also a prerequisite for multi-threaded compilation and simulation.

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

### Design decisions

- Mode flag on existing `emit_c()`, not a separate emitter. The block functions are identical — only the `veripy_eval()` dispatch wrapper changes. `emit_c(ir, mode='event')` swaps out the dispatch body.
- No mixed mode: the whole design is either cycle-accurate or event-driven. Mixing adds enormous complexity for a niche use case.

### Plan

1. C event scheduler: priority queue keyed on `(time, delta)`, ~200 lines
2. Sensitivity-driven dispatch: block runs → diff outputs → enqueue dependents
3. Change detection: compare written signals to previous values (write sets already known from `nba_per_seq` and `comb_deps`)
4. `#delay` support: TB `Delay(N)` nodes emit `schedule(time + N, callback)` instead of `_step()` loops
5. `emit_c(ir, mode='event')`: same block functions, event-driven dispatch wrapper
6. Delta-cycle convergence: iterate at current time until no pending events, with loop detection

### Expected cost

3-5× slower than the compiled path. Still 20-50× faster than vvp. Selectable via flag: `compile_bench(..., mode='event')`.

### Priority

Low — cycle-accurate covers most verification needs. Build when mixed-clock or timing-aware simulation is required.

## Waveform dumping from csim

The compiled model currently runs blind — no signal trace output. Adding opt-in waveform dumping would make csim usable for debugging, not just benchmarking.

### Design decisions

- Both VCD and FST formats. VCD is trivial to emit and universally supported. FST is compact and preferred for large traces (GTKWave supports both).
- Tracing always compiled into the model with a runtime enable flag. An `if (tracing)` check per eval is free (branch predictor). Avoids needing separate cached `.so` files for trace vs no-trace builds.
- Signal selection: dump all by default, or a user-specified subset to minimize overhead.

### Plan

1. Add `_trace` fields to `State` struct: previous-value shadow array, file handle, enable flag
2. Post-eval hook in `veripy_eval()`: compare current vs previous, write changed signals
3. VCD writer: simple `fprintf`-based, ~100 lines
4. FST writer: use `fstapi.h` from GTKWave (BSD-licensed) or minimal reimplementation
5. API: `CSimModel(trace='out.vcd')` or `compile_bench(..., trace='out.fst')`
6. Runtime toggle: `model.trace_enable(True/False)` to start/stop mid-simulation

### Priority

High — this is the main thing preventing csim from replacing vvp in day-to-day debugging workflows.

## Co-simulation with external C/SystemVerilog

Interface so csim-compiled models can be driven from external testbenches or call into user C code. This is how VeriPy models integrate into existing verification environments.

### Design decisions

- Both directions, but prioritize "VeriPy model inside external testbench" first — that's the common integration pattern (existing SV environment, drop in a fast model). Reverse direction (external C inside VeriPy) is less urgent since VeriPy has its own TB infrastructure.
- Start with stable C API (already exists: `veripy_create/destroy/eval/set_*/get_*`). DPI wrapper generation is a thin SV layer on top — add later as convenience.

### Plan

1. Formalize and document the C API as a stable interface (versioned header)
2. Add callback hooks: `veripy_register_callback(signal, fn)` for monitor-style integration
3. Support linking user `.c` files into `compile_model()`
4. Later: generate SystemVerilog DPI wrapper that maps to the C API

### Priority

Medium — needed when VeriPy models are used as components in larger verification environments.

## Coverage instrumentation in csim

Line, toggle, and FSM state coverage collected during compiled simulation. The Python sim already has coverage reporting — extending it to csim makes the fast path usable for coverage-driven verification and signoff.

### Design decisions

- VeriPy-native format (simple binary/JSON written by C, read back by Python). UCDB is Synopsys-proprietary and complex. lcov is wrong domain.
- Toggle coverage on all signals by default, with option to filter to ports only.
- Merge with existing Python coverage reporting infrastructure for unified reports.

### Plan

1. Toggle coverage: instrument each signal write with a bitmask OR tracking 0→1 and 1→0 transitions per bit
2. Line coverage: counter per comb/seq block, increment on entry
3. FSM coverage: track visited states and transitions per FSM register
4. Emit coverage data to a shared struct in the `.so`, read back via ctypes after simulation
5. Python layer merges csim coverage with Python sim coverage into unified report

### Priority

Medium — important for verification signoff but not blocking current development.

## SystemVerilog assertion support

Compile temporal assertions and cover sequences into the eval loop. The formal infrastructure (`assert_always`, `cover`) already handles single-cycle properties — temporal sequences (`##1`, `|->`, `[*N]`) would be the next step.

### Python API

Two syntaxes — method chain builder (primary) and SVA string (escape hatch):

```python
# Method chain builder — reads left-to-right like a timeline
assert_property(posedge(clk),
    seq(req).delay(1).then(ack))

assert_property(posedge(clk),
    seq(req).implies(seq(ack).within(3)))

assert_property(posedge(clk),
    seq(valid).repeat(4).then(done))

cover(posedge(clk),
    seq(fifo_full).eventually(fifo_empty))

# SVA string — for pasting from specs or SVA familiarity
assert_property(posedge(clk), "req |-> ##1 ack")
```

Both syntaxes parse to the same internal IR and compile to the same FSMs.

### Design decisions

- Assertion failures log by default (cycle number, signal values, assertion name). `halt_on_fail=True` option for debugging.
- Each assertion compiles to a small FSM tracked in the `State` struct.
- Integrate with formal backend: emit equivalent SVA for SymbiYosys.

### Plan

1. New IR nodes: `Sequence`, `Delay`, `Repeat`, `Implication`, `Within`, `Eventually`
2. `seq()` builder API returning composable `Sequence` objects
3. SVA string parser → same `Sequence` IR
4. Compile sequences to FSMs in C: one state variable + transition logic per assertion in `veripy_eval()`
5. Report: log failures with context, collect pass/fail/cover hit counts
6. Formal: emit SVA from `Sequence` IR for SymbiYosys integration

### Priority

Medium — valuable for complex protocol verification. The single-cycle `assert_always` covers most current use cases.

## Multi-threaded simulation

Partition the compiled eval across multiple cores. The flat IR dependency graph already identifies independent subgraphs — clock domains, submodule trees, and unconnected comb chains can run in parallel. The scheduler would fork independent partitions, barrier-sync at clock edges, then commit.

Probably only matters for large designs (1000+ signals) where single-core eval is the bottleneck. Verilator supports this with `--threads` and reports 2-3× speedup on big designs with good partitioning.

Per-block `.o` compilation (from incremental compilation) maps naturally to per-thread partitions.

### Priority

Low — focus on single-core optimizations first. Revisit when the CPU benchmark is running and single-core is proven to be the bottleneck.

## GPU-accelerated batch simulation

Port the compiled eval to a GPU compute shader for massively parallel parameter/seed sweeps. Not about making one simulation faster — it's about running thousands of variants simultaneously.

The WGPU batch-parallel backend already exists for the Python sim ([docs/gpu.md](docs/gpu.md)). Extending it to csim means generating WGSL from the same flat IR instead of C.

### Design decisions

- Target WGSL (WebGPU) for portability. The existing GPU backend already uses it. CUDA/Metal would be faster but platform-specific — WebGPU runs everywhere.
- Same design, different seeds/params per instance. Different testbenches per instance adds too much complexity.

### Plan

1. New emitter `emit_wgsl(ir)` targeting WebGPU compute shaders
2. Each workgroup instance gets its own `State` struct in shared memory
3. Testbench parameters (seed, config) vary per instance
4. Collect pass/fail + coverage per instance, reduce on readback
5. Reuse existing `wgpu` infrastructure from the Python GPU backend

### Priority

Low — the Python GPU backend covers this use case today. Revisit when csim performance on single instances is fully optimized and batch throughput becomes the bottleneck.

## Parameterized port arrays

Allow modules to declare a variable number of ports based on a parameter. The primary motivating use case is N-port AXI interconnects, but it applies anywhere a module needs a variable number of identical interfaces (multi-bank memories, multi-hart designs, arbiters).

### Python API

```python
@module
def axi_interconnect(NUM_MASTERS=2, DATA_WIDTH=32, ADDR_WIDTH=32):
    s_axi  = AXILiteSlave(DATA_WIDTH, ADDR_WIDTH)
    m_axi  = [AXILiteMaster(DATA_WIDTH, ADDR_WIDTH) for _ in range(NUM_MASTERS)]
    bases  = Param([int] * NUM_MASTERS)

    @comb
    def route():
        for i, m in enumerate(m_axi):
            sel = (s_axi.araddr >= bases[i]) & (s_axi.araddr < bases[i+1])
            m.araddr  = s_axi.araddr
            m.arvalid = s_axi.arvalid & sel
```

### What needs to change

1. **Lowerer** — recognize port array declarations, assign indexed names (`m_axi_0_awaddr`, `m_axi_1_awaddr`, …)
2. **IR** — represent port arrays as a sized group of port nodes, not individual ports
3. **`@comb` iteration** — allow `for` loops over port arrays to unroll at elaboration time
4. **Verilog emitter** — emit indexed flat port names; optionally emit packed arrays where synthesis tools support it
5. **Sub-module instantiation** — when a port array is connected to a sub-module, wire each index correctly

### Priority

Medium — the current workaround (hardcoded `m0_axi`, `m1_axi`, manual address split) works but doesn't scale. Needed for clean N-peripheral SoC designs.

## Project tooling and large-design ergonomics

Features needed to support large-scale designs (CPUs, SoCs, multi-module FPGA projects) from a build/workflow perspective.

### Project file and scaffolding

No declarative project configuration exists. Everything is CLI flags or Python code.

Add `veripy.toml` as the project manifest:

```toml
[project]
name = "my_soc"
top = "src/soc_top.py"
sources = ["src/", "ip/"]

[build]
output = "build/rtl"
parameters = { data_width = 32, addr_width = 16 }

[build.targets.sim]
parameters = { data_width = 32, debug_bus = true }

[build.targets.synth]
parameters = { data_width = 32, debug_bus = false }

[test]
parallel = 4
seed = "random"

[fpga]
part = "xc7a35t"
constraints = ["constraints/pins.xdc", "constraints/timing.xdc"]
```

Add `veripy init` to scaffold a new project with directory structure and starter `veripy.toml`.

Everything below builds on this — build targets, test config, FPGA targeting all live in the project file.

Priority: Critical — foundation for all other project tooling.

### Watch mode and dependency-aware incremental build

`Project.write(incremental=True)` skips unchanged files, but there is no:

- `veripy build --watch` to rebuild on file change
- Module dependency graph tracking (if sub-module A changes, rebuild parent B)
- Hash-based build caching to skip re-lint / re-compile when nothing changed

For a 50+ module design, full rebuild on every save is too slow.

Priority: High.

### Test parallelization and regression

`veripy test` shells out to `python -m unittest discover` serially.

- Run tests in parallel across cores (each test is independent — Python sim + iverilog)
- Merge coverage across parallel runs, report cumulative numbers
- Seed management for constrained random — record seeds, replay failures
- Regression mode: compare pass/fail against a saved baseline, flag new failures

Priority: High.

### Hierarchy visualization and design stats

No way to understand a design's structure without reading the code.

- `veripy graph <file.py>` → DOT/SVG block diagram of module hierarchy, port connections, signal widths
- `veripy stats <file.py>` → register count, combinational depth estimate, memory usage

Priority: Medium.

### Build targets and configurations

Real projects need multiple build configurations:

- Simulation (debug signals, assertions enabled)
- Synthesis (assertions stripped, debug removed)
- Per-board FPGA builds (different parameters)

Driven by `[build.targets.*]` sections in `veripy.toml`. Invoked as `veripy build --target sim`.

Priority: High — depends on project file.

### Cross-module lint

Current lint is per-module only. A full-hierarchy pass should catch:

- Unconnected ports at the top level
- Width mismatches at module boundaries
- Clock domain crossing violations across the hierarchy
- Combinational loops spanning multiple modules

Priority: Medium.

### Filelist and external tool integration

The `Project` class generates Vivado/Quartus/DC Tcl, but is missing:

- `filelist.f` generation (universal EDA format)
- Makefile generation (`veripy makefile` → targets for build/test/lint/formal)
- Constraint file management (XDC/SDC tracked alongside the design)
- Mixed-language support (wrapping VHDL or SystemVerilog IP)

Priority: Medium.

### IP dependency resolution

`veripy ip` is pip-based discovery only. Needed for multi-team projects:

- Version constraints (`veripy-axi >= 0.3, < 1.0`)
- Lock file for reproducible builds
- Local path dependencies (monorepo with multiple IP blocks)
- IP configuration from `veripy.toml`

Priority: Low — matters once multiple teams/repos are involved.

### Library primitives for CPU/SoC designs

Current IP library covers FIFOs, arbiters, edge detectors, clock dividers. Larger designs also need:

- AXI4 full interconnect (not just AXI4-Lite)
- Bus fabric / crossbar generator
- Interrupt controller
- DMA engine template
- Memory controller interface (DDR PHY BlackBox + controller)
- Debug transport (JTAG TAP BlackBox + debug module)

Priority: Low — build as needed by real designs.

## FPGA build flow

VeriPy currently emits Verilog and generates Tcl scripts, but doesn't drive synthesis or produce bitstreams. A full FPGA build flow would let you go from Python to programmed FPGA in one command.

### Board and platform definitions

Declarative board descriptions: FPGA part, pin maps, clock sources, IO standards. Used by the constraint manager and synthesis drivers.

### Constraint management

Pins, timing, and IO standards as first-class objects (Python or YAML), not loose XDC/SDC files. Constraints travel with the design and are validated against the board definition.

### Synthesis drivers

- Yosys+nextpnr for open-source targets (iCE40, ECP5, Gowin) — high priority, no vendor licenses needed
- Vivado batch mode driver — medium priority
- Quartus batch mode driver — medium priority

### CLI

`veripy build --target <board>` selects the board, applies constraints, invokes the appropriate synthesis flow, and produces a bitstream. `veripy program` flashes it via iceprog/openFPGALoader.

### Built-in boards

Ship definitions for common dev boards: iCEBreaker, ULX3S, Arty A7, DE10-Nano.

Priority: High — needed to close the loop from design to hardware.

## Declarative SoC builder

Define SoCs in YAML/JSON. Generate interconnect, address decode, CSRs, C headers, linker scripts, and documentation from one config file.

### SoC YAML schema

```yaml
cpu:
  type: rv32i
  isa: [m, c]

bus: axi4lite
address_width: 32
data_width: 32

memory:
  - name: sram
    base: 0x00000000
    size: 64K

peripherals:
  - name: uart0
    type: uart
    base: 0x10000000
    params: { baud: 115200 }
  - name: spi0
    type: spi_controller
    base: 0x20000000
    params: { width: 8, fifo_depth: 16 }
  - name: gpio0
    type: gpio
    base: 0x30000000
    params: { width: 32 }

platform:
  board: icebreaker
  clock: 12MHz
```

### What it generates

- Top-level module: CPU + peripherals + interconnect, fully wired
- AXI4-Lite crossbar with address decode logic
- Memory map with overlap detection
- C headers: register addresses, bitfield macros, peripheral base addresses
- Linker script from memory map
- Documentation: memory map table, peripheral summary

### CLI

`veripy soc build <config.yaml>` — emit Verilog for the full SoC.

### Comparison to existing tools

- LiteX: Python-imperative SoC builder (Migen-based). Powerful but steep learning curve, no declarative config.
- Topwrap (Antmicro): YAML-based block assembly, but wraps existing RTL — doesn't own the IP definitions.
- VeriPy advantage: owns the IP (VeriPy modules), so it can simulate the full SoC natively, generate interconnect, and build bitstreams from one tool.

Priority: Critical — the centerpiece feature for SoC design.

## Firmware co-simulation

Load firmware ELF into simulated SoC, run on CPU model, interact with peripherals. Boot and debug firmware before silicon exists.

- ELF loader: parse sections, load into simulated memory
- SoC simulator wrapper: assemble CPU + peripherals + memory into runnable sim
- UART/console bridge: capture peripheral output during simulation
- GDB stub: remote debug firmware on simulated CPU
- CLI: `veripy soc sim <config.yaml> --firmware <firmware.elf>`

Supports csim and Verilator backends for performance.

Priority: High — the payoff of the SoC builder. Depends on soc-builder.

## Bus functional models

Reusable transaction-level bus models for driving and monitoring bus transactions from testbenches.

- AXI4-Lite master/slave BFM
- AXI4 full master/slave BFM (burst, outstanding transactions)
- Wishbone and APB BFMs
- Bus monitor: protocol checker, transaction logger, coverage collector

Priority: Medium — needed for SoC-level verification.

## FPGA-in-the-loop

Synthesize design to FPGA, drive it from the same Python testbench used for simulation. Bridge between host Python and FPGA via UART/JTAG/USB.

- Host-FPGA communication bridge
- Testbench adapter: same API targeting sim or hardware
- Signal sampling and stimulus injection

Priority: Low — advanced verification capability. Depends on fpga-build.
