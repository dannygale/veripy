"""Constrained random stimulus generation.

Thin wrapper around the ``constrainedrandom`` library.  Install with::

    pip install constrainedrandom

Example::

    from veripy.rand import Rand, Range

    r = Rand(addr=Range(0, 0xFFF), data=Range(0, 255, exclude=[0]))
    r.randomize()
    print(r.addr, r.data)
"""

try:
    import constrainedrandom as _cr
except ImportError:
    _cr = None


class Range:
    """Domain specification for a random variable."""
    __slots__ = ('lo', 'hi', 'exclude')

    def __init__(self, lo, hi, *, exclude=None):
        self.lo = lo
        self.hi = hi
        self.exclude = exclude or []


class Rand:
    """Constrained random object. Fields become attributes after randomize().

    Args:
        **fields: name=Range(...) pairs defining random variables.
    """

    def __init__(self, **fields):
        if _cr is None:
            raise ImportError('constrainedrandom is required: pip install constrainedrandom')
        self._obj = _cr.RandObj()
        self._names = list(fields.keys())
        for name, rng in fields.items():
            domain = range(rng.lo, rng.hi + 1)
            self._obj.add_rand_var(name, domain=domain)
            for exc in rng.exclude:
                self._obj.add_constraint(lambda v, e=exc: v != e, (name,))

    def add_constraint(self, fn, *var_names):
        """Add an arbitrary constraint. *fn* receives the named vars as args."""
        self._obj.add_constraint(fn, var_names if var_names else tuple(self._names))

    def randomize(self):
        """Solve constraints and update attributes."""
        self._obj.randomize()
        for name in self._names:
            setattr(self, name, getattr(self._obj, name))

    def as_dict(self):
        """Return current values as a dict."""
        return {name: getattr(self, name) for name in self._names}
