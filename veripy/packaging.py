"""IP packaging: discover installed VeriPy IP packages and scaffold new ones."""

import os
from dataclasses import dataclass, field
from importlib.metadata import entry_points, metadata

ENTRY_POINT_GROUP = 'veripy.ip'


@dataclass
class IpMetadata:
    """Metadata for an installed VeriPy IP package."""
    name: str
    version: str
    module_path: str  # dotted import path to the IP module
    description: str = ''
    ip_modules: list[str] = field(default_factory=list)

    def load(self):
        """Import and return the IP module."""
        import importlib
        return importlib.import_module(self.module_path)


def discover() -> list[IpMetadata]:
    """Find all installed VeriPy IP packages via entry points.

    IP packages register under the ``veripy.ip`` entry-point group::

        [project.entry-points."veripy.ip"]
        my_ip = "veripy_my_ip"

    Returns a sorted list of IpMetadata.
    """
    results = []
    eps = entry_points()
    group = eps.get(ENTRY_POINT_GROUP, []) if isinstance(eps, dict) else eps.select(group=ENTRY_POINT_GROUP)
    for ep in group:
        dist = ep.dist
        meta = metadata(dist.name) if dist else None
        version = dist.metadata['Version'] if dist else 'unknown'
        desc = (dist.metadata.get('Summary', '') if dist else '')
        results.append(IpMetadata(
            name=ep.name,
            version=version,
            module_path=ep.value,
            description=desc,
        ))
    results.sort(key=lambda m: m.name)
    return results


def scaffold(name: str, output_dir: str = '.') -> str:
    """Generate a minimal VeriPy IP package skeleton.

    Creates::

        <output_dir>/<name>/
        ├── pyproject.toml
        ├── README.md
        └── veripy_<name>/
            └── __init__.py

    Returns the path to the created package directory.
    """
    pkg_name = f'veripy-{name}'
    mod_name = f'veripy_{name}'
    root = os.path.join(output_dir, name)
    mod_dir = os.path.join(root, mod_name)
    os.makedirs(mod_dir, exist_ok=True)

    # pyproject.toml
    pyproject = f'''[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{pkg_name}"
version = "0.1.0"
description = "VeriPy IP package: {name}"
requires-python = ">=3.10"
dependencies = ["veripy-hdl"]

[project.entry-points."veripy.ip"]
{name} = "{mod_name}"
'''
    with open(os.path.join(root, 'pyproject.toml'), 'w') as f:
        f.write(pyproject)

    # README
    with open(os.path.join(root, 'README.md'), 'w') as f:
        f.write(f'# {pkg_name}\n\nVeriPy IP package: {name}\n')

    # __init__.py with example module
    init_src = f'''"""VeriPy IP package: {name}."""

# Define your VeriPy modules here, e.g.:
#
# from veripy import Module, Input, Output, Register
#
# class MyBlock(Module):
#     def __init__(self, width=8):
#         self.clock = Input()
#         self.reset = Input()
#         self.data_in = Input(width)
#         self.data_out = Output(width)
#         self.reg = Register(width)
#         super().__init__()
#
#         @self.posedge(self.clock)
#         def logic():
#             if self.reset:
#                 self.reg = 0
#             else:
#                 self.reg = self.data_in
#
#         @self.comb
#         def output():
#             self.data_out = self.reg
'''
    with open(os.path.join(mod_dir, '__init__.py'), 'w') as f:
        f.write(init_src)

    return root
