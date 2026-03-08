# Testing

## Test Case Types

VeriPy provides four test case classes for different testing needs:

| Class | Purpose | Backend |
|---|---|---|
| `TestBench` | Functional tests with multi-backend comparison | csim (default) |
| `VeripyTestCase` | Same as `TestBench` with `backend='all'` | all backends |
| `BehavioralTestCase` | Combinational / intent tests, no timing | Python only |
| `FirmwareTestCase` | ELF-based CPU / SoC tests | behavioral + csim |

---

## TestBench — Functional Testing

Write one test, verify Python simulation and RTL produce identical outputs:

```python
from veripy import TestBench

class TestCounter(TestBench):
    def create_module(self):
        return counter(width=4)

    def test_counting(self):
        self.clock('clock', period=10)

        @self.initial
        def stimulus():
            self.set(reset=1, enable=1)
            yield 10
            self.assertEqual(self.out('count'), 0)
            self.set(reset=0)
            for _ in range(5):
                yield 10
            self.assertEqual(self.out('count'), 5)
```

Or use the linear coroutine style with `run_testbench`:

```python
    def test_counting(self):
        @self.run_testbench(clock='clock', period=10)
        def run():
            self.set(reset=1, enable=1)
            yield 10
            self.assertEqual(self.out('count'), 0)
            self.set(reset=0)
            for _ in range(5):
                yield 10
            self.assertEqual(self.out('count'), 5)
```

`@self.run_testbench(clock, period)` registers the clock and runs the simulation — no separate `@self.always` clock block needed.

### TestBench API

```python
self.clock(name, period=10)      # register a clock driver
self.set(**kwargs)               # set input signal values
self.out(name)                   # read output and record for comparison
self.get(name)                   # read signal (works in peripheral callbacks too)
self.module                      # direct access to the module under test
self.run_sim()                   # run the simulation
self.reset()                     # reset module and engine (fresh state)
self.run_testbench(clock, period) # decorator: clock + initial + run_sim in one
self.peripheral(fn)              # register a memory/bus callback (see below)
self.fork(*fns)                  # parallel blocks, wait for all
self.fork_any(*fns)              # parallel blocks, wait for first
self.coverage_report()           # [(name, hit_count)] for cover points
```

### Peripheral Callbacks

For modules with external memory or bus interfaces (e.g. a CPU pipeline), register
a `peripheral` callback that fires every time unit during behavioral sim and after
every `model.eval()` during RTL replay:

```python
class TestPipeline(TestBench):
    def create_module(self): return Pipeline(reset_pc=0)

    def test_addi(self):
        imem = {0: encode_i(42, 0, 0, 1, OP_IMM)}

        @self.peripheral
        def memory():
            addr = self.get('imem_addr') & ~3
            self.set(imem_data=imem.get(addr, NOP))

        @self.run_testbench(clock='clock', period=10)
        def run():
            yield 60
            self.assertEqual(self.out('rd1'), 42)
```

The peripheral function uses `self.get()` to read signals and `self.set()` to write
them. During RTL replay, these are automatically redirected to the compiled model.

### Controlling Backends

```python
class TestMyModule(TestBench):
    backend = 'check'      # behavioral + csim (default)
    # backend = 'fast'     # csim only
    # backend = 'thorough' # csim + iverilog
    # backend = 'all'      # all backends
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

---

## BehavioralTestCase — Combinational / Intent Testing

For combinational modules or pure behavioral models — no clock, no timing, just
set inputs and check outputs:

```python
from veripy import BehavioralTestCase
from src.alu import alu, ADD, SUB

class TestAlu(BehavioralTestCase):
    def create_module(self): return alu()

    def test_add(self):
        self.set(op=ADD, a=3, b=4)
        self.assertEqual(self.out('result'), 7)

    def test_sub(self):
        self.set(op=SUB, a=10, b=3)
        self.assertEqual(self.out('result'), 7)
```

`set()` evaluates comb and `@behavioral` blocks immediately. No `yield`, no
`run_sim()`. Runs in pure Python — fast and simple.

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
