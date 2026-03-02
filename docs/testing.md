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

Each `test_*` method runs three ways:
1. Python simulation — assertions against the Python model
2. Sim vs RTL — stimulus replayed through iverilog, outputs compared cycle-by-cycle
3. Verilog — assertions against iverilog outputs

```
$ veripy test tests/ -v
```

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
