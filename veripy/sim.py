"""Simulation primitives: reactive wait sentinels and VCD waveform writer."""


class Until:
    """Sentinel yielded by generators to wait for a condition.

    Usage in testbench generators::

        yield until(lambda: int(dut.ready) == 1)
        yield until(lambda: int(dut.ready) == 1, timeout=1000)
    """
    __slots__ = ('cond', 'timeout')

    def __init__(self, cond, timeout=None):
        self.cond = cond
        self.timeout = timeout


def until(cond, timeout=None):
    """Wait until *cond()* returns truthy. Raise TimeoutError after *timeout* time units."""
    return Until(cond, timeout)


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
