"""Equivalence checking backend: generate Yosys script for equiv_check.

Compares two Verilog modules (gold and gate) to formally prove they produce
identical outputs for all possible inputs.  Requires Yosys on PATH.
"""

from .ir import IRModule
from .backend_verilog import emit_verilog


def emit_equiv_script(gold: IRModule, gate: IRModule) -> str:
    """Generate a Yosys equivalence checking script.

    Both modules must have the same ports (names, directions, widths).
    The gold module is renamed to ``gold`` and the gate to ``gate``
    before running ``equiv_make`` / ``equiv_simple`` / ``equiv_status``.

    Returns the Yosys script as a string.
    """
    _check_ports_match(gold, gate)

    lines = [
        f'read_verilog gold.v',
        f'rename {gold.name} gold',
        f'read_verilog gate.v',
        f'rename {gate.name} gate',
        '',
        'equiv_make gold gate equiv',
        'prep -top equiv',
        'equiv_simple',
        'equiv_induct',
        'equiv_status -assert',
    ]
    return '\n'.join(lines) + '\n'


def _check_ports_match(gold: IRModule, gate: IRModule):
    """Raise ValueError if the two modules have incompatible port signatures."""
    gold_ports = {(p.name, p.direction, p.width) for p in gold.ports}
    gate_ports = {(p.name, p.direction, p.width) for p in gate.ports}
    if gold_ports != gate_ports:
        missing = gold_ports - gate_ports
        extra = gate_ports - gold_ports
        parts = []
        if missing:
            parts.append(f'missing from gate: {_fmt_ports(missing)}')
        if extra:
            parts.append(f'extra in gate: {_fmt_ports(extra)}')
        raise ValueError(f'Port mismatch: {"; ".join(parts)}')


def _fmt_ports(ports):
    return ', '.join(f'{d} {n}[{w}]' for n, d, w in sorted(ports))
