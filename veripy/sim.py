"""Event-driven simulation engine with Verilog scheduling semantics."""

import heapq


class SimEngine:
    """Event-driven simulator for a Module.

    Testbench blocks are Python generators that yield time delays.
    The engine schedules them on a priority queue and processes
    signal changes with proper Verilog scheduling regions:
    active (comb/blocking) → NBA (non-blocking) → re-settle.
    """

    def __init__(self, module):
        self.mod = module
        self.time = 0
        self._queue = []       # min-heap of (time, seq, gen, restart_fn)
        self._seq = 0          # tie-breaker for heap ordering
        self._finished = False
        self._initial_count = 0  # track active initial blocks

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

    def finish(self):
        """Stop the simulation (like $finish)."""
        self._finished = True

    def run(self):
        """Run the simulation until all initial blocks complete or finish() is called."""
        # Snapshot initial signal state
        self.mod._snapshot_prev()
        self.mod._settle_comb()

        while self._queue and not self._finished:
            t, _, gen, restart_fn = heapq.heappop(self._queue)
            self.time = t

            # Resume the generator — it runs until next yield
            try:
                delay = next(gen)
                self._schedule(t + delay, gen, restart_fn)
            except StopIteration:
                if restart_fn is not None:
                    # Always block: restart from the top
                    self._schedule(t, restart_fn(), restart_fn)
                else:
                    # Initial block finished
                    self._initial_count -= 1
                    if self._initial_count <= 0:
                        self._finished = True

            # Process signal changes at this time step
            self._process()

    def _schedule(self, time, gen, restart_fn):
        heapq.heappush(self._queue, (time, self._seq, gen, restart_fn))
        self._seq += 1

    def _process(self):
        """Verilog scheduling: active → detect edges → fire blocks → NBA → re-settle."""
        mod = self.mod

        # Active region: settle comb with current signal values
        mod._settle_comb()

        # Detect edges
        triggered = mod._check_edges()

        if triggered:
            # Run triggered always blocks (writes go to NBA)
            for _edges, method in triggered:
                method()

            # NBA region: apply non-blocking assignments
            mod._apply_nba()
            for sub in mod._submodules().values():
                sub._apply_nba()

            # Re-settle comb with new register values
            mod._settle_comb()

        # Snapshot for next edge detection
        mod._snapshot_prev()
