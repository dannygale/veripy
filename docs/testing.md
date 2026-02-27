# Testing

## Dual-Path Testing

Write one test, verify both Python simulation and generated Verilog produce the same outputs:

```python
from veripy import VeripyTestCase

class TestCounter(VeripyTestCase):
    def create_module(self):
        return counter(width=4)

    def test_counting(self):
        self.set(reset=1, enable=1)
        self.tick()
        self.assertEqual(self.out('count'), 0)
        self.set(reset=0)
        for _ in range(5):
            self.tick()
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

@sim.initial
def stimulus():
    c.reset.set(1)
    c.enable.set(1)
    yield 1
    c.reset.set(0)
    for _ in range(5):
        yield 1
    assert int(c.count) == 5

sim.run()
```

`@sim.initial` blocks run once. `@sim.always` blocks restart on completion. `yield N` advances N time units. Simulation ends when all initial blocks finish or `sim.finish()` is called.

## VCD Waveforms

Dump waveforms viewable in GTKWave:

```python
sim = SimEngine(c, vcd='counter.vcd')
sim.run()
```
