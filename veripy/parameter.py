"""Parameter: deferred width placeholder for parameterized modules."""


def _vname(x):
    if isinstance(x, (Parameter, ParamExpr)):
        return x.name
    return str(x)


class ParamExpr:
    """Deferred arithmetic expression on Parameters."""
    __slots__ = ('_fn', '_deps', 'name')

    def __init__(self, fn, deps, name=None):
        self._fn = fn
        self._deps = deps
        self.name = name

    def resolve(self, param_values):
        return self._fn(param_values)

    @property
    def default(self):
        return self._fn({p.name: p.default for p in self._deps})

    def _binop(self, other, op, op_str):
        n = f'{_vname(self)}{op_str}{_vname(other)}'
        if isinstance(other, Parameter):
            return ParamExpr(lambda v, s=self, o=other, f=op: f(s.resolve(v), v[o.name]),
                             self._deps + [other], n)
        if isinstance(other, ParamExpr):
            return ParamExpr(lambda v, s=self, o=other, f=op: f(s.resolve(v), o.resolve(v)),
                             self._deps + other._deps, n)
        return ParamExpr(lambda v, s=self, o=other, f=op: f(s.resolve(v), o),
                         self._deps, n)

    def __add__(self, o):      return self._binop(o, lambda a, b: a + b, '+')
    def __radd__(self, o):     return self._binop(o, lambda a, b: b + a, '+')
    def __sub__(self, o):      return self._binop(o, lambda a, b: a - b, '-')
    def __rsub__(self, o):     return self._binop(o, lambda a, b: b - a, '-')
    def __mul__(self, o):      return self._binop(o, lambda a, b: a * b, '*')
    def __rmul__(self, o):     return self._binop(o, lambda a, b: b * a, '*')
    def __floordiv__(self, o): return self._binop(o, lambda a, b: a // b, '/')


class Parameter:
    """Deferred width placeholder resolved at module instantiation."""
    __slots__ = ('default', 'name')

    def __init__(self, default=None):
        self.default = default
        self.name = None

    def resolve(self, param_values):
        return param_values[self.name]

    def _binop(self, other, op, op_str):
        n = f'{_vname(self)}{op_str}{_vname(other)}'
        if isinstance(other, Parameter):
            return ParamExpr(lambda v, s=self, o=other, f=op: f(v[s.name], v[o.name]),
                             [self, other], n)
        if isinstance(other, ParamExpr):
            return ParamExpr(lambda v, s=self, o=other, f=op: f(v[s.name], o.resolve(v)),
                             [self] + other._deps, n)
        return ParamExpr(lambda v, s=self, o=other, f=op: f(v[s.name], o),
                         [self], n)

    def __add__(self, o):      return self._binop(o, lambda a, b: a + b, '+')
    def __radd__(self, o):     return self._binop(o, lambda a, b: b + a, '+')
    def __sub__(self, o):      return self._binop(o, lambda a, b: a - b, '-')
    def __rsub__(self, o):     return self._binop(o, lambda a, b: b - a, '-')
    def __mul__(self, o):      return self._binop(o, lambda a, b: a * b, '*')
    def __rmul__(self, o):     return self._binop(o, lambda a, b: b * a, '*')
    def __floordiv__(self, o): return self._binop(o, lambda a, b: a // b, '/')


def is_param(value):
    """Check if a value is a Parameter or ParamExpr."""
    return isinstance(value, (Parameter, ParamExpr))
