"""Context registry and standalone decorators for @module API."""

import threading
from .signal import Signal, Edge, SensitivityList, posedge as _posedge, negedge as _negedge

_local = threading.local()


def _get_context():
    return getattr(_local, 'current', None)


def _set_context(ctx):
    _local.current = ctx


class ModuleContext:
    """Collects signals, logic blocks, and sub-modules during @module execution."""

    def __init__(self):
        self.signals = {}
        self.mems = {}
        self.submodules = {}
        self.comb_blocks = []
        self.always_blocks = []
        self.assertions = []
        self.covers = []
        self.assumes = []
        self.timing = []


# --- Logic block decorators ---

def comb(fn):
    """Standalone @comb decorator — registers a combinational block."""
    ctx = _get_context()
    if ctx is not None:
        ctx.comb_blocks.append(fn)
    else:
        fn._veripy_comb = True
    return fn


def always(sensitivity):
    """Standalone @always(sensitivity) decorator — multi-edge sensitivity list.

    Usage:
        @always(posedge(clk) | negedge(rst_n))
        def async_reset():
            if not rst_n:
                reg = 0
            else:
                reg = d
    """
    if isinstance(sensitivity, Edge):
        edges = [sensitivity]
    elif isinstance(sensitivity, SensitivityList):
        edges = sensitivity.edges
    else:
        raise TypeError(f"Expected Edge or SensitivityList, got {type(sensitivity)}")

    def decorator(fn):
        ctx = _get_context()
        if ctx is not None:
            ctx.always_blocks.append((edges, fn))
        return fn
    return decorator


# --- FSM ---

def fsm(clock, reset, states):
    """Standalone @fsm decorator — declarative state machine.

    Usage:
        @fsm(clock, reset, states=['IDLE', 'RUN', 'DONE'])
        def ctrl(state, IDLE, RUN, DONE):
            if state == IDLE:
                if start:
                    return RUN
            elif state == RUN:
                return DONE
    """
    width = max(1, (len(states) - 1).bit_length())
    state_reg = Signal(width, _kind='reg', name='_fsm_state')
    next_state = Signal(width, _kind='wire', name='_fsm_next')
    state_vals = {name: i for i, name in enumerate(states)}

    def decorator(func):
        func.__globals__.update(state_vals)

        def comb_wrapper():
            ns = func(int(state_reg), **state_vals)
            next_state._assign(ns if ns is not None else int(state_reg))

        comb_wrapper.__wrapped__ = func
        comb_wrapper._fsm_info = {
            'states': states, 'state_vals': state_vals,
            'state_reg': state_reg, 'next_state': next_state,
            'width': width,
        }

        ctx = _get_context()
        if ctx is not None:
            ctx.comb_blocks.append(comb_wrapper)
            # Register the posedge update block
            edge = _posedge(clock)

            def fsm_update():
                if reset:
                    state_reg._assign(0)
                else:
                    state_reg._assign(int(next_state))

            ctx.always_blocks.append(([edge], fsm_update))
            # Register FSM signals on context
            ctx.signals['_fsm_state'] = state_reg
            ctx.signals['_fsm_next'] = next_state

        return func
    return decorator


# --- Formal properties ---

def assert_always(clock):
    """Standalone @assert_always(clock) decorator — formal assertion checked every cycle.

    Usage:
        @assert_always(clock)
        def bounded():
            return int(count) < 16
    """
    def decorator(func):
        ctx = _get_context()
        if ctx is not None:
            ctx.assertions.append((clock, func))
        return func
    return decorator


def cover(clock):
    """Standalone @cover(clock) decorator — coverage point.

    Usage:
        @cover(clock)
        def reaches_five():
            return int(count) == 5
    """
    def decorator(func):
        ctx = _get_context()
        if ctx is not None:
            ctx.covers.append((clock, func, [0]))
        return func
    return decorator


def assume(clock):
    """Standalone @assume(clock) decorator — formal assumption.

    Usage:
        @assume(clock)
        def valid_input():
            return int(enable) | int(reset)
    """
    def decorator(func):
        ctx = _get_context()
        if ctx is not None:
            ctx.assumes.append((clock, func))
        return func
    return decorator


# --- Pipeline ---

def pipeline(clock, reset, width=1, *, stall=None, flush=None, valid_in=None):
    """Context-aware pipeline — creates pipeline registers and returns a _Pipeline.

    Usage:
        pipe = pipeline(clock, reset, width=16)
        pipe.stage(lambda: int(a) + int(b))
        pipe.stage(lambda prev: prev * 2)

        @comb
        def output():
            out = pipe.result

    Multi-value stages return tuples; the next stage unpacks them:
        pipe.stage(lambda: (a, b))
        pipe.stage(lambda a, b: a + b)

    Optional *stall*, *flush*, *valid_in* add flow control.
    """
    from .module import _Pipeline, Module

    # Create a minimal Module-like object for _Pipeline to attach to
    ctx = _get_context()
    if ctx is None:
        raise RuntimeError("pipeline() must be called inside a @module function")

    class _PipelineHost:
        """Minimal host for _Pipeline that collects blocks on the context."""
        def __setattr__(self, name, value):
            if isinstance(value, Signal):
                value.name = name
                ctx.signals[name] = value
            object.__setattr__(self, name, value)

        def comb(self, method):
            ctx.comb_blocks.append(method)
            return method

        def posedge(self, clock_signal):
            edge = _posedge(clock_signal)
            def decorator(method):
                ctx.always_blocks.append(([edge], method))
                return method
            return decorator

    host = _PipelineHost()
    return _Pipeline(host, clock, reset, width,
                     stall=stall, flush=flush, valid_in=valid_in)


# --- Timing constraints ---

def create_clock(signal, period_ns):
    """Declare a clock with the given period (ns)."""
    ctx = _get_context()
    if ctx is not None:
        name = signal.name if signal.name else signal
        ctx.timing.append(('create_clock', name, period_ns))


def max_delay(from_signal, to_signal, ns):
    """Set a max delay constraint between two signals."""
    ctx = _get_context()
    if ctx is not None:
        # Store signal refs — names resolved at collection time
        ctx.timing.append(('max_delay', from_signal, to_signal, ns))


def false_path(from_signal, to_signal):
    """Declare a false path between two signals."""
    ctx = _get_context()
    if ctx is not None:
        ctx.timing.append(('false_path', from_signal, to_signal))
