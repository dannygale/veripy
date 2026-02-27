"""Context registry and standalone decorators for @module API."""

import threading
from .signal import Edge, SensitivityList, posedge as _posedge, negedge as _negedge

_local = threading.local()


def _get_context():
    return getattr(_local, 'current', None)


def _set_context(ctx):
    _local.current = ctx


class ModuleContext:
    """Collects signals, logic blocks, and sub-modules during @module execution."""
    __slots__ = ('signals', 'mems', 'submodules', 'comb_blocks', 'always_blocks')

    def __init__(self):
        self.signals = {}
        self.mems = {}
        self.submodules = {}
        self.comb_blocks = []
        self.always_blocks = []


def comb(fn):
    """Standalone @comb decorator — registers a combinational block on the current context."""
    ctx = _get_context()
    if ctx is not None:
        ctx.comb_blocks.append(fn)
    else:
        # Mark for later collection (class-level usage)
        fn._veripy_comb = True
    return fn


def posedge(signal):
    """Standalone @posedge(signal) decorator — registers a sequential block."""
    edge = _posedge(signal) if not isinstance(signal, str) else signal

    def decorator(fn):
        ctx = _get_context()
        if ctx is not None:
            if isinstance(edge, str):
                fn._veripy_posedge = edge
            else:
                ctx.always_blocks.append(([edge], fn))
        else:
            fn._veripy_posedge = edge
        return fn
    return decorator


def negedge(signal):
    """Standalone @negedge(signal) decorator — registers a sequential block."""
    edge = _negedge(signal) if not isinstance(signal, str) else signal

    def decorator(fn):
        ctx = _get_context()
        if ctx is not None:
            if isinstance(edge, str):
                fn._veripy_negedge = edge
            else:
                ctx.always_blocks.append(([edge], fn))
        else:
            fn._veripy_negedge = edge
        return fn
    return decorator
