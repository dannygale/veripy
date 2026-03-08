# Testing

## Dual-Path Testing

Write one test, verify both Python simulation and generated Verilog produce the same outputs:

```python
from veripy import VeripyTestCase

class TestCounter(VeripyTestCase):
    def create_module(self):
        return counter(width=4)

    def test_counting(self):
        @self.always
        def clock():
            self.set(clock=0); yield 5
            self.set(clock=1); yield 5

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

Each `test_*` method automatically runs against up to five backends, and all outputs are compared:

1. **Python simulation** — assertions run against the Python model
2. **iverilog** — stimulus replayed through iverilog, outputs compared cycle-by-cycle
3. **csim (flat)** — stimulus replayed through the native C simulation backend
4. **csim (hierarchical)** — same as csim but forces hierarchical per-module compilation
5. **Verilator** (opt-in) — stimulus replayed through Verilator co-simulation

Backends 3–4 run automatically. Backend 5 is opt-in via `USE_VERILATOR = True` on the test class or `VERIPY_VERILATOR=1` environment variable.

After all backends run, `_assert_all_match()` compares every output at every timestep across all backends — any mismatch is a test failure.

```
$ veripy test tests/ -v
```

### Controlling Backends

```python
class TestMyModule(VeripyTestCase):
    SKIP_CSIM = True       # skip csim backends for this test class
    USE_VERILATOR = True   # enable Verilator backend

    def create_module(self):
        return my_module()
```

Environment variables:
- `VERIPY_SKIP_CSIM=1` — skip csim backends globally
- `VERIPY_VERILATOR=1` — enable Verilator backend globally

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

`@sim.initial` blocks run once. `@sim.always` blocks restart on completion. `yield N` advances N time units. `sim.clock(signal, period)` generates a free-running clock. Simulation ends when all initial blocks finish or `sim.finish()` is called.

## VCD Waveforms

Dump waveforms viewable in GTKWave:

```python
sim = SimEngine(c, vcd='counter.vcd')
sim.run()
```

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

Works in both `SimEngine` blocks and `VeripyTestCase` initial blocks.

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

In `VeripyTestCase`, use `self.fork()` and `self.fork_any()`.

## Native C Simulation (csim)

The csim backend compiles your design to native C for near-Verilator performance. It runs automatically in `VeripyTestCase` — no setup needed.

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

Subclass `Driver` to build reusable transaction-level helpers that drive and monitor bus protocols. Drivers use `yield` and `until()` internally — call them with `yield from` in testbench blocks:

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

Override `send(txn)`, `recv()`, and optionally `reset()`. See [`examples/spi_driver.py`](../examples/spi_driver.py) for a complete example.

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

In `VeripyTestCase`, use `self.coverage_report()`.
