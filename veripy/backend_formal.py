"""Formal verification backend: generate .sby files for SymbiYosys."""

from .ir import IRModule
from .backend_verilog import emit_verilog


def emit_sby(ir: IRModule, *, depth: int = 20, engine: str = 'smtbmc') -> str:
    """Generate a SymbiYosys .sby configuration file for the given IR module.

    Returns the .sby file content as a string.
    """
    has_assert = any(p.kind == 'assert' for p in ir.formal_props)
    has_cover = any(p.kind == 'cover' for p in ir.formal_props)

    tasks = []
    if has_assert:
        tasks.append('bmc')
    if has_cover:
        tasks.append('cover')

    lines = []

    if tasks:
        lines.append('[tasks]')
        for t in tasks:
            lines.append(t)
        lines.append('')

    lines.append('[options]')
    if has_assert:
        lines.append(f'bmc: mode bmc')
        lines.append(f'bmc: depth {depth}')
    if has_cover:
        lines.append(f'cover: mode cover')
        lines.append(f'cover: depth {depth}')
    if not tasks:
        lines.append(f'mode bmc')
        lines.append(f'depth {depth}')
    lines.append('')

    lines.append('[engines]')
    lines.append(engine)
    lines.append('')

    lines.append('[script]')
    lines.append(f'read_verilog -formal {ir.name}.v')
    lines.append(f'prep -top {ir.name}')
    lines.append('')

    lines.append('[files]')
    lines.append(f'{ir.name}.v')

    return '\n'.join(lines) + '\n'


def emit_formal_verilog(ir: IRModule) -> str:
    """Emit Verilog with FORMAL defined (formal properties included)."""
    return emit_verilog(ir)
