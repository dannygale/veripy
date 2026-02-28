"""Event-driven simulation engine with Verilog scheduling semantics."""

import heapq


class VCDWriter:
    """Writes VCD (Value Change Dump) files for waveform viewing."""

    def __init__(self, f, module, timescale='1ns'):
        self._f = f
        self._signals = []  # [(id_char, signal, width, name)]
        self._prev = {}     # id -> last written value
        self._write_header(module, timescale)

    def _id_gen(self):
        """Generate short VCD identifiers: !, \", #, ..., then !!, !\", ..."""
        n = 0
        while True:
            s, i = '', n
            while True:
                s = chr(33 + (i % 94)) + s
                i = i // 94 - 1
                if i < 0:
                    break
            yield s
            n += 1

    def _collect_signals(self, module, prefix, scope_lines, var_lines, ids):
        scope_lines.append(f'$scope module {prefix or module.__class__.__name__.lower()} $end')
        for name, sig in sorted(module._signals().items()):
            vid = next(ids)
            self._signals.append((vid, sig, sig.width, f'{prefix}.{name}' if prefix else name))
            self._prev[vid] = None
            bits = f' [{sig.width-1}:0]' if sig.width > 1 else ''
            var_lines.append(f'$var wire {sig.width} {vid} {name}{bits} $end')
        scope_lines += var_lines
        var_lines.clear()
        for sub_name, sub in sorted(module._submodules().items()):
            self._collect_signals(sub, f'{prefix}.{sub_name}' if prefix else sub_name,
                                  scope_lines, var_lines, ids)
        scope_lines.append('$upscope $end')

    def _write_header(self, module, timescale):
        w = self._f.write
        w(f'$timescale {timescale} $end\n')
        scope_lines, ids = [], self._id_gen()
        self._collect_signals(module, '', scope_lines, [], ids)
        w('\n'.join(scope_lines) + '\n')
        w('$enddefinitions $end\n')
        w('$dumpvars\n')
        for vid, sig, width, _name in self._signals:
            self._prev[vid] = sig._val
            w(f'b{sig._val:0{width}b} {vid}\n')
        w('$end\n')

    def record(self, time):
        """Write value changes for the current timestep."""
        changes = []
        for vid, sig, width, _name in self._signals:
            v = sig._val
            if v != self._prev[vid]:
                changes.append(f'b{v:0{width}b} {vid}\n')
                self._prev[vid] = v
        if changes:
            self._f.write(f'#{time}\n')
            self._f.writelines(changes)


class SimEngine:
    """Event-driven simulator for a Module.

    Testbench blocks are Python generators that yield time delays.
    The engine schedules them on a priority queue and processes
    signal changes with proper Verilog scheduling regions:
    active (comb/blocking) → NBA (non-blocking) → re-settle.
    """

    def __init__(self, module, vcd=None):
        self.mod = module
        self.time = 0
        self._queue = []       # min-heap of (time, seq, gen, restart_fn)
        self._seq = 0          # tie-breaker for heap ordering
        self._finished = False
        self._initial_count = 0  # track active initial blocks
        self._vcd_path = vcd
        self._vcd = None
        self._vcd_file = None

    def initial(self, fn):
        """Register a generator function as an initial block (runs once)."""
        gen = fn()
        self._initial_count += 1
        self._schedule(0, gen, None)
        return fn

    def always(self, fn):
        """Register a generator function as an always block (restarts on completion)."""
        gen = fn()
        self._schedule(0, gen, fn)
        return fn

    def clock(self, signal, period=10):
        """Register a clock driver. Toggles signal every period/2 time units."""
        half = period // 2
        @self.always
        def _clk():
            signal.set(0)
            yield half
            signal.set(1)
            yield half

    def finish(self):
        """Stop the simulation (like $finish)."""
        self._finished = True

    def run(self):
        """Run the simulation until all initial blocks complete or finish() is called."""
        if self._vcd_path:
            self._vcd_file = open(self._vcd_path, 'w')
            self._vcd = VCDWriter(self._vcd_file, self.mod)

        try:
            self.mod._init_sim_cache()
            self.mod._snapshot_prev()
            self.mod._settle_comb()

            while self._queue and not self._finished:
                t, _, gen, restart_fn = heapq.heappop(self._queue)
                self.time = t

                try:
                    delay = next(gen)
                    self._schedule(t + delay, gen, restart_fn)
                except StopIteration:
                    if restart_fn is not None:
                        self._schedule(t, restart_fn(), restart_fn)
                    else:
                        self._initial_count -= 1
                        if self._initial_count <= 0:
                            self._finished = True

                self._process()
        finally:
            if self._vcd_file:
                self._vcd_file.close()
                self._vcd_file = None

    def _schedule(self, time, gen, restart_fn):
        heapq.heappush(self._queue, (time, self._seq, gen, restart_fn))
        self._seq += 1

    def _process(self):
        """Verilog scheduling: active → detect edges → fire blocks → NBA → re-settle."""
        mod = self.mod
        sig_list = mod._sig_list
        mem_list = mod._mem_list
        comb_blocks = mod._comb_blocks
        sub_settle = mod._sub_settle_info

        # --- settle comb (pass 1) ---
        for method in comb_blocks:
            method()
        for sig in sig_list:
            sig._tick()
        for mem in mem_list:
            mem._tick()
        for sub_nba, sub_comb in sub_settle:
            sub_nba()
        for sub_nba, sub_comb in sub_settle:
            for method in sub_comb:
                method()
            sub_nba()
        # --- settle comb (pass 2) ---
        for method in comb_blocks:
            method()
        for sig in sig_list:
            sig._tick()
        for mem in mem_list:
            mem._tick()

        # --- edge detection ---
        triggered = mod._check_edges()

        if triggered:
            for _edges, method in triggered:
                method()

            # NBA
            for sig in sig_list:
                sig._tick()
            for mem in mem_list:
                mem._tick()
            for sub_nba, _ in sub_settle:
                sub_nba()

            # re-settle
            for method in comb_blocks:
                method()
            for sig in sig_list:
                sig._tick()
            for mem in mem_list:
                mem._tick()
            for sub_nba, sub_comb in sub_settle:
                sub_nba()
            for sub_nba, sub_comb in sub_settle:
                for method in sub_comb:
                    method()
                sub_nba()
            for method in comb_blocks:
                method()
            for sig in sig_list:
                sig._tick()
            for mem in mem_list:
                mem._tick()

            mod._check_assertions()

        # --- snapshot ---
        for sig in sig_list:
            sig._prev_val = sig._val
        for _, sub_sigs in mod._sub_sig_lists:
            for sig in sub_sigs:
                sig._prev_val = sig._val

        if self._vcd:
            self._vcd.record(self.time)


class BehavioralSim:
    """Fast behavioral simulator that runs @self.behavioral functions.

    Falls back to cycle-accurate (_settle_comb + posedge) for modules
    without a behavioral model.
    """

    def __init__(self, module):
        self.mod = module

    def step(self):
        """Advance one step: call behavioral model or fall back to sim."""
        mod = self.mod
        if mod._behavioral is not None:
            mod._behavioral()
        else:
            mod._settle_comb()
            triggered = mod._check_edges()
            if triggered:
                for _edges, method in triggered:
                    method()
                mod._apply_nba()
                mod._settle_comb()
            mod._snapshot_prev()
