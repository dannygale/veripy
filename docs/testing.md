# Testing

## Test Case Types

VeriPy provides two test case classes for different testing needs:

| Class | Purpose | Backend |
|---|---|---|
| `TestBench` | Multi-tier tests (functional/cycle/RTL) with cross-checking | cysim (default) |
| `FirmwareTestCase` | ELF-based CPU / SoC tests | behavioral + csim |

---

## TestBench — Functional Testing

Write one test, verify all model tiers produce identical outputs:

```python
from veripy.verify import TestBench, initial

class TestCounter(TestBench):
    def create_module(self):
        return counter(width=4)

    def test_counting(self):
        dut = self.dut
        self.clock('clock', period=10)

        @initial
        def stimulus():
            dut.reset = 1; dut.enable = 1
            yield 10
            assert dut.count == 0
            dut.reset = 0
            for _ in range(5):
                yield 10
            assert dut.count == 5
```

### Signal Namespace — `self.dut`

`self.dut` provides a clean signal namespace for tests:

```python
dut = self.dut
dut.reset = 1          # sets input signal (calls self.set('reset', 1))
dut.enable = 0         # sets input signal
val = dut.count        # reads output signal value (int)
assert dut.count == 5  # compare directly
```

In cysim mode, `dut.reset = 1` calls `CySimModel.set('reset', 1)` directly — no Python simulation overhead.

### Free-Standing Decorators

Use `@initial` and `@always` as free-standing decorators (not `@self.initial`):

```python
from veripy.verify import TestBench, initial, always

class TestMyModule(TestBench):
    def test_something(self):
        dut = self.dut
        self.clock('clock', 10)

        @initial
        def stimulus():
            dut.reset = 1
            yield 10
            dut.reset = 0
```

These use thread-local context to find the current `TestBench` instance. Imported from `veripy.verify` or `veripy`.

### TestBench API

```python
self.dut                         # signal namespace (dut.x = val sets, dut.x reads)
self.clock(name, period=10)      # register a clock driver
self.module                      # direct access to the module under test
self.run_sim()                   # run the simulation (auto-called by wrapper)
self.run_cycles(n)               # run N clock cycles in pure compiled C
self.set(**kwargs)               # set input signals (bulk, e.g. self.set(a=1, b=2))
self.get(name)                   # read signal value
self.reset()                     # reset module and engine (fresh state)
self.peripheral(fn)              # register a memory/bus callback (see below)
self.fork(*fns)                  # parallel blocks, wait for all
self.fork_any(*fns)              # parallel blocks, wait for first
self.coverage_report()           # [(name, hit_count)] for cover points
```

### Batch Execution — `run_cycles()`

For performance-critical tests, `run_cycles(n)` runs N full clock cycles entirely
in compiled C with no Python interaction:

```python
def test_long_run(self):
    dut = self.dut
    self.clock('clock', 10)

    @initial
    def stim():
        dut.reset = 1
        yield 10
        dut.reset = 0; dut.enable = 1
        self.run_cycles(100000)    # 100k cycles in pure C
        assert dut.count == 100000 & 0xF
```

### Peripheral Callbacks

For modules with external memory or bus interfaces (e.g. a CPU pipeline), register
a `peripheral` callback that fires every time unit during simulation:

```python
class TestPipeline(TestBench):
    def create_module(self): return Pipeline(reset_pc=0)

    def test_addi(self):
        dut = self.dut
        imem = {0: encode_i(42, 0, 0, 1, OP_IMM)}
        self.clock('clock', 10)

        @self.peripheral
        def memory():
            addr = self.get('imem_addr') & ~3
            self.set(imem_data=imem.get(addr, NOP))

        @initial
        def stim():
            dut.reset = 1
            yield 10
            dut.reset = 0
            yield 60
            assert dut.rd1 == 42
```

The peripheral function uses `self.get()` and `self.set()` for signal access.
During cysim execution, these are automatically redirected to the compiled model.

### Controlling Backends

```python
class TestMyModule(TestBench):
    backend = 'cysim'      # Cython-compiled direct execution (default)
    # backend = 'behavioral' # Python sim only
    # backend = 'check'     # behavioral + csim, cross-check outputs
    # backend = 'all'       # all backends
    SKIP_CSIM = True       # skip csim for this class
    USE_VERILATOR = True   # enable Verilator backend
    vcd_on_fail = True     # dump VCD when any assertion fails
```

Environment variables:
- `VERIPY_BACKENDS=csim,iverilog` — override backend selection globally
- `VERIPY_SKIP_CSIM=1` — skip csim backends globally
- `VERIPY_VERILATOR=1` — enable Verilator backend globally
- `VERIPY_VCD_ON_FAIL=1` — dump VCD on failure globally

Each `test_*` method runs against the configured backends and all outputs are
compared cycle-by-cycle. Any mismatch is a test failure.

```
$ veripy test tests/ -v
```

### Multi-Model Dispatch

When a module defines multiple simulation layers (`@functional`, `@cycle`, RTL),
`TestBench` automatically runs the test against each available layer and
cross-checks outputs at every observation point.

**Model fidelity order:** `functional` < `cycle` < `rtl`

#### Default (no `model` attribute)

All layers the module defines are exercised:

```python
class TestMyModule(TestBench):
    def create_module(self):
        return my_module()   # defines @functional + RTL → runs both
```

#### Pinned floor (`model` attribute)

Set a minimum fidelity floor — the test requires at least this layer:

```python
class TestPipelineStall(TestBench):
    model = 'cycle'   # skips functional; runs cycle and rtl
```

The CLI can raise the floor but not lower it below the class value.

#### CLI override

```
veripy test                          # all available models per module
veripy test --model functional       # functional only; skip pinned tests requiring more
veripy test --model cycle            # cycle and above
veripy test --model rtl              # RTL only
```

Or via environment variable: `VERIPY_MODEL=rtl veripy test`.

#### Cross-check

When multiple models run, outputs are compared across all models at every sampled
time point. A mismatch fails the test with a diff showing which model diverged.

---

## Cysim — Cython-Compiled Simulation

The cysim backend is the default for `TestBench`. It compiles your module's IR to C
(via csim), then wraps it in a Cython-compiled event loop (`CySimEngine`) that runs
the entire simulation — scheduling, eval, time advance — in native C.

Performance vs Python simulation:
- **yield-per-cycle**: ~40× faster (event loop in C, Python yields each cycle)
- **run_cycles()**: ~120× faster (entire batch in C, no Python interaction)

Cysim compiles on first use and caches the `.so` by content hash. Subsequent runs
skip compilation entirely.

For combinational-only testing (no clock, no timing), use the `@functional` model
tier — it evaluates instantly in Python without needing a simulation engine.

---

## FirmwareTestCase — ELF / CPU Testing

For CPU and SoC designs. Loads an ELF, runs until the firmware writes to `tohost`
(standard riscv-tests halt protocol), and checks the result:

```python
from veripy import FirmwareTestCase
from veripy.soc import SocConfig
from src.pipeline import Pipeline

class TestCompliance(FirmwareTestCase):
    tohost_addr = 0x1000

    def create_cpu(self): return Pipeline()
    def create_soc(self): return SocConfig.minimal(ram_size=0x10000)

    def _step(self):
        """Advance CPU one cycle, servicing memory via self._soc."""
        pc = self._soc.memory.read(self._cpu_pc_addr)
        instr = self._soc.read(pc)
        # ... drive CPU inputs, tick clock

    def test_add(self):
        self.load_elf('tests/rv32ui-p-add')
        self.run_until_halt(timeout=20_000)
        self.assertEqual(self.tohost(), 1)   # 1 = pass in riscv-tests

    def test_rv32ui_suite(self):
        self.run_arch_suite('tests/rv32ui-p-*.elf')
```

`run_arch_suite(pattern)` discovers all matching ELFs and runs each as a `subTest`,
reporting individual pass/fail per ELF. This replaces custom compliance runner scripts.

### FirmwareTestCase API

```python
self.load_elf(path)              # load ELF into SocSim memory
self.run_until_halt(timeout)     # run until tohost write or timeout
self.tohost()                    # return tohost value (1 = pass)
self.run_arch_suite(glob)        # run all matching ELFs as subtests
self._soc                        # SocSim instance (memory + peripherals)
self._step()                     # override: advance CPU one cycle
```

---

## Behavioral ↔ RTL Equivalence Checking

For modules with both a `@behavioral` block and `@comb`/`@always` RTL, VeriPy can
automatically fuzz-test them against each other using Hypothesis:

```
$ veripy check alu.py
Checking alu (behavioral vs csim)...
  alu: OK (200 examples)

$ veripy check alu.py -n 1000
```

No test code required. VeriPy generates random inputs for all input signals,
runs both paths, and reports the minimal failing case if they diverge.

Requires `pip install hypothesis`.

---

## SimEngine

Event-driven simulation with proper Verilog scheduling semantics:

```python
from veripy.sim import SimEngine

c = counter(width=4)
sim = SimEngine(c)
sim.clock(c.clock, 10)

@sim.initial
def stimulus():
    c.reset.set(1)
    c.enable.set(1)
    yield 10
    c.reset.set(0)
    for _ in range(5):
        yield 10
    assert int(c.count) == 5

sim.run()
```

`@sim.initial` blocks run once. `@sim.always` blocks restart on completion.
`yield N` advances N time units. `sim.clock(signal, period)` generates a
free-running clock. Simulation ends when all initial blocks finish or
`sim.finish()` is called.

## VCD Waveforms

Dump waveforms viewable in GTKWave:

```python
sim = SimEngine(c, vcd='counter.vcd')
sim.run()
```

From `TestBench`, enable on failure:

```python
class TestMyModule(TestBench):
    vcd_on_fail = True   # writes <ClassName>_<test_name>.vcd on assertion failure
```

Or globally: `VERIPY_VCD_ON_FAIL=1`.

## Reactive Waits

`yield until(cond)` suspends a block until a condition becomes true, checked each time unit:

```python
from veripy.sim import SimEngine, until

@sim.initial
def stimulus():
    c.enable.set(1)
    yield until(lambda: int(c.count) == 10)    # wait for count to reach 10
    c.enable.set(0)
```

Optional timeout raises `TimeoutError`:

```python
yield until(lambda: int(c.done), timeout=1000)
```

Works in both `SimEngine` blocks and `TestBench` initial blocks.

## Fork / Join

Run multiple generator blocks in parallel:

```python
@sim.initial
def test():
    # fork() waits for ALL blocks to finish
    yield sim.fork(drive_clock, send_data)

    # fork_any() waits for the FIRST block to finish
    yield sim.fork_any(wait_for_done, timeout_block)
```

Each forked function is a generator (uses `yield`):

```python
def send_data():
    for byte in data:
        c.tx_data.set(byte)
        c.tx_valid.set(1)
        yield 10
        c.tx_valid.set(0)
        yield until(lambda: int(c.tx_ready))
```

In `TestBench`, use `self.fork()` and `self.fork_any()`.

## Native C Simulation (csim)

The csim backend compiles your design to native C for near-Verilator performance.
It runs automatically in `TestBench` — no setup needed.

For standalone use:

```python
from veripy import CSimModel

with CSimModel(my_module, 'my_module') as model:
    model.set('enable', 1)
    model.eval()
    print(model.get('count'))
```

### VCD Tracing from csim

```python
with CSimModel(my_module, 'my_module', trace='out.vcd') as model:
    for _ in range(100):
        model.set('clock', 0); model.eval()
        model.set('clock', 1); model.eval()
# out.vcd written on context exit
```

## Protocol Drivers

Subclass `Driver` to build reusable transaction-level helpers that drive and monitor
bus protocols. Drivers use `yield` and `until()` internally — call them with
`yield from` in testbench blocks:

```python
from veripy.driver import Driver
from veripy.sim import until

class SpiDriver(Driver):
    def send(self, data):
        m = self.mod
        m.cs_n.set(0)
        for bit in range(7, -1, -1):
            m.mosi.set((data >> bit) & 1)
            m.sclk.set(0); yield 5
            m.sclk.set(1); yield 5
        m.sclk.set(0)
        m.cs_n.set(1)
        yield 5

    def recv(self):
        m = self.mod
        yield until(lambda: int(m.rx_valid) == 1)
        return int(m.rx_data)
```

Usage in a testbench:

```python
drv = SpiDriver(sim, dut)

@sim.initial
def stim():
    yield from drv.send(0xA5)
    val = yield from drv.recv()
```

Override `send(txn)`, `recv()`, and optionally `reset()`. See
[`examples/spi_driver.py`](../examples/spi_driver.py) for a complete example.

## Constrained Random

Generate random stimulus with constraints:

```python
from veripy.rand import Rand, Range

r = Rand(
    addr=Range(0, 0xFFFF),
    size=Range(1, 4, exclude=[3]),     # 1, 2, or 4
    burst=Range(0, 2),
)
r.add_constraint(lambda addr, size: addr % size == 0, 'addr', 'size')

for _ in range(100):
    r.randomize()
    c.addr.set(r.addr)
    c.size.set(r.size)
    yield 10
```

Requires `pip install constrainedrandom`.

## Coverage Reporting

`@cover` points track whether conditions were hit during simulation:

```python
from veripy.context import cover

@cover(clock)
def fifo_full():
    return int(fifo.full)

# After simulation:
for name, count in m.coverage_report().items():
    print(f'{name}: hit {count} times')
```

In `TestBench`, use `self.coverage_report()`.
