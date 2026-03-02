# GPU Simulation (WGPU Backend)

VeriPy can simulate designs on the GPU using WebGPU compute shaders. Each GPU thread runs an independent instance of your design, enabling batch-parallel verification — thousands of stimulus vectors evaluated in a single dispatch.

## Installation

The GPU backend is an optional dependency:

```
pip install veripy[gpu]
```

This installs [wgpu-py](https://github.com/pygfx/wgpu-py) and NumPy.

## Quick Start

```python
from veripy import module, Input, Output, Register, posedge
from veripy.context import comb, always

@module
def counter(width=8):
    clock  = Input()
    reset  = Input()
    enable = Input()
    count  = Output(width)
    cnt    = Register(width)

    @comb
    def drive():
        count = cnt

    @always(posedge(clock))
    def increment():
        if reset:
            cnt = 0
        elif enable:
            cnt = cnt + 1
```

```python
from veripy import GpuSim
from veripy.lower import lower_module

ir = lower_module(counter(width=4))
gpu = GpuSim(ir, n_instances=1)

gpu.step(clock=0, reset=1, enable=1)
gpu.step(clock=1, reset=1, enable=1)
gpu.step(clock=0, reset=0, enable=1)
gpu.step(clock=1, reset=0, enable=1)
print(gpu.read('count'))  # → array([1])
```

## GpuSim API

### Constructor

```python
GpuSim(ir: IRModule, n_instances: int = 1, params: dict | None = None)
```

- `ir` — lowered IR from `lower_module()`
- `n_instances` — number of parallel design instances (each gets independent state)
- `params` — parameter overrides (defaults to `ir.params`)

### step(\*\*inputs)

Run one simulation cycle. Input values can be scalars (broadcast to all instances) or NumPy arrays of length `n_instances`:

```python
gpu.step(clock=1, reset=0, enable=1)           # scalar — same for all instances
gpu.step(clock=np.array([1, 0, 1, 0]))         # per-instance values
```

### read(name) → np.ndarray

Read a signal's current value across all instances:

```python
vals = gpu.read('count')  # shape: (n_instances,)
```

### read_mem(name, addr) → np.ndarray

Read one memory element across all instances:

```python
gpu.read_mem('ram', 0)  # element 0 of 'ram' for each instance
```

### reset()

Zero all state across all instances.

### bulk_run(traces, cycles) → dict

Run multiple cycles with predetermined stimulus. Returns output traces:

```python
import numpy as np

traces = {
    'clock':  np.tile([0, 1], 50),          # 100 half-cycles
    'reset':  np.array([1, 1, 0, 0, ...]),
    'enable': np.ones(100, dtype=np.uint32),
}
results = gpu.bulk_run(traces, cycles=100)
print(results['count'])  # shape: (100, n_instances)
```

Input arrays are shape `(cycles,)` for broadcast or `(cycles, n_instances)` for per-instance stimulus.

## Sub-Modules and Flattening

WGSL compute shaders cannot instantiate sub-modules. If your design uses hierarchy, flatten it first with `flatten_ir`:

```python
from veripy.flatten import flatten_ir
from veripy.lower import lower_module

parent_ir = lower_module(wrapper())
child_ir  = lower_module(counter(width=4))

flat = flatten_ir(parent_ir, {'counter': child_ir})
gpu  = GpuSim(flat, n_instances=256)
```

`flatten_ir(parent, registry)` inlines all `Instance` nodes:
- Child signals are prefixed with the instance name (e.g., `c0_cnt`)
- Port connections are resolved through the parent's wire map
- Memory arrays are prefixed and carried into the flat module

## WGSL Code Generation

The `emit_wgsl` function converts IR to a WGSL compute shader:

```python
from veripy.backend_wgpu import emit_wgsl

wgsl_source, signal_names, mem_layout = emit_wgsl(ir)
```

Returns:
- `wgsl_source` — the WGSL shader text
- `signal_names` — ordered list of signal names (struct field order)
- `mem_layout` — list of `(name, depth, width)` for memory arrays

The generated shader has this structure:
1. `State` struct — all signals as `u32` fields, memory arrays as `array<u32, N>`
2. `eval_comb()` — evaluates combinational logic
3. `eval_seq()` — evaluates sequential blocks (edge-triggered)
4. `main()` — entry point: loads stimulus, runs comb → seq → comb, writes back state

## Limitations

- **32-bit max width** — all signals must fit in `u32`
- **No sub-modules** — flatten hierarchy with `flatten_ir` before GPU simulation
- **Concrete widths only** — all signal widths must resolve to integers (no unresolved parameters)
- **No tri-state or X/Z** — GPU simulation uses unsigned integer semantics only

## When to Use GPU Simulation

GPU simulation is most useful for:
- **Batch verification** — running thousands of random stimulus vectors in parallel
- **Parameter sweeps** — simulating many configurations simultaneously
- **Regression testing** — accelerating large test suites with independent test vectors

For single-instance interactive debugging, the Python `SimEngine` or Verilator co-simulation are better choices.
