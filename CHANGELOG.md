# Changelog

All notable changes to VeriPy are documented in this file.

## [0.2.0] — 2026-03-02

### Added

#### Core
- **BlackBox** — port-only wrappers for vendor/external IP with parameter forwarding
- **Memory inference** — `Mem(style='block'|'distributed'|'ultra')` with `(* ram_style *)` attributes; `DualPortMem` and `TrueDualPortMem` variants
- **Lazy expressions** — signal operators return composable `_Expr` objects with AST tracking, inline Verilog emission, and automatic width inference in `@comb` blocks
- **Named-stage pipelines** — `pipe.stage('name', stall, flush, field=source)` for CPU-style pipelines with per-stage flow control

#### IP Library
- **AXI4-Lite subordinate** — parameterized read/write decode, byte enables, response signaling
- **CSR register maps** — `RegisterMap`/`Reg`/`Field` generating RTL, C headers, Python test drivers, and markdown docs
- **Parameterized IPs** — `SyncFifo`, `EdgeDetector`, `Debouncer`, `RoundRobinArbiter`, `PriorityArbiter`, `ClockDivider`, `CreditFlowControl`

#### Verification
- **SymbiYosys formal** — emit `.sby` files from `assert_always`/`cover`/`assume`; bounded model checking with VCD counterexamples (`veripy formal`)
- **Equivalence checking** — prove Python model matches emitted Verilog via Yosys `equiv_check` (`veripy equiv`)
- **Verilator co-simulation** — compile to shared lib, drive from Python via ctypes; 100–1000× faster than Python sim
- **Enhanced testing** — reactive yields (`yield until(cond)`), `fork`/`join`, constrained random, `Driver` base class, coverage reporting

#### Lint
- **Combinational loop detection**
- **Latch inference warnings**
- **Clock domain annotation** — `clock_domain()` API with automatic CDC violation flagging

#### Tooling
- **Build system** — `Project` class with dependency ordering, Tcl generation for Vivado/Quartus/DC, incremental rebuild
- **Auto-docs** — generate markdown from module definitions (`veripy doc`)
- **IP packaging** — pip-installable IP packages with discovery and scaffold (`veripy ip`)
- **GPU simulation** — WebGPU backend for batch-parallel verification

### Changed
- IR pipeline replaces `VerilogEmitter`: `Module` → `lower()` → `IRModule` → `emit()`
- One `.v` file per module instead of monolithic output
- Unified edge API: `@always(posedge(clk))` everywhere, removed `@posedge`/`@negedge`
- All simulation through `SimEngine` — removed `Module.tick()` and `simulate()`
- Parametric Verilog emission carries symbolic parameter names through to output
- SimEngine 48× performance improvement

### Fixed
- FSM state register codegen for multi-transfer sequences
- `Module.__setattr__` handling of underscore-prefixed Signal attributes
- Emitter submodule port discovery via `_signals()`
- Testbench lowering: loops instead of unrolling, correct `tc_var` detection

## [0.1.0] — 2025

### Added
- **Core framework** — `Signal`, `Input`, `Output`, `Register`, `Module`, `@module` decorator
- **Verilog emission** — synthesizable output with parameterized widths
- **Dual-path testing** — `VeripyTestCase` runs each test in Python sim and iverilog, compared cycle-by-cycle
- **SimEngine** — event-driven simulator with Verilog scheduling semantics and VCD dump
- **Sub-modules** — hierarchical composition with automatic port wiring and parameter overrides
- **Bit slicing** — read/write for both simulation and Verilog
- **Mem** — memory arrays with combinational read and synchronous write
- **FSM sugar** — `@self.fsm` for declarative state machines
- **Interface bundles** — reusable signal groups with parameterized widths, bulk connect, forwarding
- **Formal properties** — `assert_always`, `cover`
- **Timing annotations** — `create_clock`, `max_delay` with SDC output
- **Pipeline transforms** — lambda-chain API with explicit stage boundaries
- **Verilog import** — convert existing RTL to VeriPy, including cross-file projects
- **Lint** — undriven outputs, multi-driven signals, missing reset, unused signals, CDC violations
- **CDC primitives** — `Synchronizer`, `AsyncFIFO` with gray-code pointers
- **CLI** — `veripy build`, `veripy test`, `veripy import`, `veripy lint`, `veripy profile`
- **SPI controller example** — pipelined SPI master with TX FIFO
