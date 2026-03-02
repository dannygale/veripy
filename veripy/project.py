"""Project: tracks modules and provides dependency-ordered file lists."""

import re
from .module import Module


def _to_snake(name):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).lower()


class Project:
    """Tracks modules and their sub-module dependencies.

    Provides dependency-ordered module lists for build tools.
    BlackBox modules are excluded (their definitions come from vendor libs).

    Usage::

        proj = Project()
        proj.add(top_module)
        for name, verilog in proj.file_list():
            write(f"{name}.v", verilog)
    """

    def __init__(self):
        self._tops = []  # [(name, module)]

    def add(self, module, name=None):
        """Add a top-level module to the project."""
        if name is None:
            name = _to_snake(type(module).__name__)
        self._tops.append((name, module))
        return self

    def modules(self):
        """Return all modules in dependency order (leaves first).

        BlackBox modules are excluded.
        """
        ordered = []
        seen = set()

        def _walk(mod, mod_name):
            if mod_name in seen:
                return
            seen.add(mod_name)
            for _sub_attr, sub in mod._submodules().items():
                if getattr(sub, '_is_blackbox', False):
                    continue
                sub_snake = _to_snake(type(sub).__name__)
                factory = getattr(type(sub), '_veripy_factory', None)
                fresh = factory() if factory else type(sub)()
                _walk(fresh, sub_snake)
            ordered.append((mod_name, mod))

        for name, mod in self._tops:
            _walk(mod, name)

        return ordered

    def file_list(self):
        """Return dependency-ordered ``(name, verilog_source)`` pairs."""
        return [(name, mod.to_verilog(name)) for name, mod in self.modules()]
