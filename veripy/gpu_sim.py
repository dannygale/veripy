"""GPU simulation runner using wgpu-py (WebGPU).

Dispatches WGSL compute shaders to simulate N independent instances of a
design in parallel on the GPU.  Each call to step() evaluates one cycle.

Usage:
    from veripy.lower import lower_module
    from veripy.gpu_sim import GpuSim

    ir = lower_module(Counter(n=4))
    gpu = GpuSim(ir, n_instances=1)
    gpu.step(clock=0, reset=1, enable=1)
    gpu.step(clock=1, reset=1, enable=1)
    gpu.step(clock=0, reset=0, enable=1)
    gpu.step(clock=1, reset=0, enable=1)
    print(gpu.read('count'))  # → array([1])
"""

import numpy as np

try:
    import wgpu
    import wgpu.utils
except ImportError:
    wgpu = None

from .backend_wgpu import emit_wgsl
from .ir import IRModule


class GpuSim:
    """Cycle-accurate GPU simulator for an IRModule."""

    def __init__(self, ir: IRModule, n_instances: int = 1, params: dict | None = None):
        if wgpu is None:
            raise ImportError('wgpu-py is required for GPU simulation: pip install wgpu')

        self.ir = ir
        self.n = n_instances
        wgsl_src, self.sig_names = emit_wgsl(ir, params)
        self._sig_idx = {name: i for i, name in enumerate(self.sig_names)}
        self._inputs = {p.name for p in ir.ports if p.direction == 'input'}
        self._outputs = {p.name for p in ir.ports if p.direction == 'output'}

        # Struct has padding to multiple of 4 fields
        self._stride = len(self.sig_names) + (4 - len(self.sig_names) % 4) % 4

        # Host-side state: flat u32 arrays matching the State struct layout
        self._state = np.zeros((n_instances, self._stride), dtype=np.uint32)
        self._stim = np.zeros((n_instances, self._stride), dtype=np.uint32)

        # wgpu setup
        self._device = wgpu.utils.get_default_device()
        self._shader = self._device.create_shader_module(code=wgsl_src)

        buf_size = self._state.nbytes
        self._stim_buf = self._device.create_buffer(size=buf_size, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST)
        self._state_buf = self._device.create_buffer(size=buf_size, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.COPY_SRC)

        bind_group_layout = self._device.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": wgpu.ShaderStage.COMPUTE, "buffer": {"type": wgpu.BufferBindingType.read_only_storage}},
            {"binding": 1, "visibility": wgpu.ShaderStage.COMPUTE, "buffer": {"type": wgpu.BufferBindingType.storage}},
        ])
        self._bind_group = self._device.create_bind_group(layout=bind_group_layout, entries=[
            {"binding": 0, "resource": {"buffer": self._stim_buf}},
            {"binding": 1, "resource": {"buffer": self._state_buf}},
        ])
        pipeline_layout = self._device.create_pipeline_layout(bind_group_layouts=[bind_group_layout])
        self._pipeline = self._device.create_compute_pipeline(layout=pipeline_layout, compute={"module": self._shader, "entry_point": "main"})

    def step(self, **inputs):
        """Run one simulation cycle. Pass input signals as keyword arguments.

        Values can be scalars (broadcast to all instances) or arrays of length n_instances.
        """
        for name, val in inputs.items():
            if name not in self._sig_idx:
                raise KeyError(f'Unknown signal: {name}')
            idx = self._sig_idx[name]
            if np.isscalar(val):
                self._stim[:, idx] = int(val)
            else:
                self._stim[:, idx] = np.asarray(val, dtype=np.uint32)

        # Upload stimulus and current state
        self._device.queue.write_buffer(self._stim_buf, 0, self._stim.tobytes())
        self._device.queue.write_buffer(self._state_buf, 0, self._state.tobytes())

        # Dispatch
        encoder = self._device.create_command_encoder()
        compute_pass = encoder.begin_compute_pass()
        compute_pass.set_pipeline(self._pipeline)
        compute_pass.set_bind_group(0, self._bind_group)
        workgroups = (self.n + 63) // 64
        compute_pass.dispatch_workgroups(workgroups)
        compute_pass.end()
        self._device.queue.submit([encoder.finish()])

        # Read back state
        data = self._device.queue.read_buffer(self._state_buf)
        self._state = np.frombuffer(data, dtype=np.uint32).reshape(self.n, self._stride).copy()

    def read(self, name: str) -> np.ndarray:
        """Read current value of a signal across all instances."""
        return self._state[:, self._sig_idx[name]]

    def reset(self):
        """Zero all state."""
        self._state.fill(0)

    def bulk_run(self, traces: dict[str, np.ndarray], cycles: int) -> dict[str, np.ndarray]:
        """Run multiple cycles with predetermined stimulus.

        Args:
            traces: mapping of input_name → array of shape (cycles,) or (cycles, n_instances)
            cycles: number of cycles to simulate

        Returns:
            dict mapping output_name → array of shape (cycles, n_instances)
        """
        out_names = [p.name for p in self.ir.ports if p.direction == 'output']
        results = {name: np.zeros((cycles, self.n), dtype=np.uint32) for name in out_names}

        for cyc in range(cycles):
            stim = {}
            for name, trace in traces.items():
                stim[name] = trace[cyc] if trace.ndim > 1 else int(trace[cyc])
            self.step(**stim)
            for name in out_names:
                results[name][cyc] = self.read(name)

        return results
