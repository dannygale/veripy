"""Auto-documentation generator for VeriPy modules.

Introspects Module instances to extract ports, parameters, registers,
sub-modules, memories, FSM states, and interfaces — then renders markdown.
"""

from .signal import Signal, Mem, DualPortMem, TrueDualPortMem, Interface
from .module import Module
from .csr import RegisterMap


def _kind_order(kind):
    return {'input': 0, 'output': 1, 'reg': 2, 'wire': 3}.get(kind, 4)


def introspect(mod):
    """Extract structured metadata from a Module instance.

    Returns a dict with keys: name, params, ports, registers, wires,
    submodules, interfaces, memories, fsm, regmaps.
    """
    name = type(mod).__name__
    sigs = mod._signals()

    ports = []
    registers = []
    wires = []
    for sname, sig in sorted(sigs.items()):
        entry = {'name': sname, 'width': sig.width, 'kind': sig._kind}
        if sig._kind in ('input', 'output'):
            ports.append(entry)
        elif sig._kind == 'reg':
            registers.append(entry)
        else:
            wires.append(entry)
    ports.sort(key=lambda e: _kind_order(e['kind']))

    # Parameters
    params = dict(getattr(mod, '_params', {}))

    # Sub-modules
    submodules = []
    for sname, sub in sorted(mod._submodules().items()):
        submodules.append({'name': sname, 'type': type(sub).__name__})

    # Interfaces
    interfaces = []
    for iname, iface in sorted(mod._interfaces().items()):
        isigs = []
        for sn, sig in sorted(iface._signals().items()):
            isigs.append({'name': sn, 'width': sig.width, 'kind': sig._kind})
        interfaces.append({'name': iname, 'signals': isigs})

    # Memories
    memories = []
    for mname, mem in sorted(mod._mems().items()):
        entry = {'name': mname, 'type': type(mem).__name__}
        if isinstance(mem, TrueDualPortMem):
            entry['depth'] = mem.depth
            entry['width'] = mem.width
        elif isinstance(mem, DualPortMem):
            entry['depth'] = mem.depth
            entry['width'] = mem.width
        elif isinstance(mem, Mem):
            entry['depth'] = mem.depth
            entry['width'] = mem.width
        memories.append(entry)

    # FSM states
    fsm = None
    for block in getattr(mod, '_comb_blocks', []):
        info = getattr(block, '_fsm_info', None)
        if info:
            fsm = {'states': list(info['states']), 'width': info['width']}
            break

    # Register maps (attributes that are RegisterMap instances)
    regmaps = []
    for attr in dir(mod):
        if attr.startswith('_'):
            continue
        val = getattr(mod, attr, None)
        if isinstance(val, RegisterMap):
            regmaps.append({'name': attr, 'map': val})

    return {
        'name': name,
        'params': params,
        'ports': ports,
        'registers': registers,
        'wires': wires,
        'submodules': submodules,
        'interfaces': interfaces,
        'memories': memories,
        'fsm': fsm,
        'regmaps': regmaps,
    }


def to_markdown(mod, module_name=None):
    """Generate markdown documentation for a Module instance."""
    info = introspect(mod)
    name = module_name or info['name']
    lines = [f'# {name}', '']

    # Parameters
    if info['params']:
        lines.append('## Parameters')
        lines.append('')
        lines.append('| Name | Default |')
        lines.append('|------|---------|')
        for pname, pval in sorted(info['params'].items()):
            lines.append(f'| {pname} | {pval} |')
        lines.append('')

    # Ports
    inputs = [p for p in info['ports'] if p['kind'] == 'input']
    outputs = [p for p in info['ports'] if p['kind'] == 'output']
    if inputs or outputs:
        lines.append('## Ports')
        lines.append('')
        lines.append('| Name | Direction | Width |')
        lines.append('|------|-----------|-------|')
        for p in inputs:
            lines.append(f"| {p['name']} | input | {p['width']} |")
        for p in outputs:
            lines.append(f"| {p['name']} | output | {p['width']} |")
        lines.append('')

    # Registers
    if info['registers']:
        lines.append('## Registers')
        lines.append('')
        lines.append('| Name | Width |')
        lines.append('|------|-------|')
        for r in sorted(info['registers'], key=lambda e: e['name']):
            lines.append(f"| {r['name']} | {r['width']} |")
        lines.append('')

    # Memories
    if info['memories']:
        lines.append('## Memories')
        lines.append('')
        lines.append('| Name | Type | Depth | Width |')
        lines.append('|------|------|-------|-------|')
        for m in info['memories']:
            lines.append(f"| {m['name']} | {m['type']} | {m['depth']} | {m['width']} |")
        lines.append('')

    # FSM
    if info['fsm']:
        states = info['fsm']['states']
        lines.append('## FSM')
        lines.append('')
        lines.append(f"States ({len(states)}): {', '.join(states)}")
        lines.append('')

    # Interfaces
    if info['interfaces']:
        lines.append('## Interfaces')
        lines.append('')
        for iface in info['interfaces']:
            lines.append(f"### {iface['name']}")
            lines.append('')
            lines.append('| Signal | Direction | Width |')
            lines.append('|--------|-----------|-------|')
            for s in iface['signals']:
                lines.append(f"| {s['name']} | {s['kind']} | {s['width']} |")
            lines.append('')

    # Sub-modules
    if info['submodules']:
        lines.append('## Sub-modules')
        lines.append('')
        lines.append('| Instance | Type |')
        lines.append('|----------|------|')
        for s in info['submodules']:
            lines.append(f"| {s['name']} | {s['type']} |")
        lines.append('')

    # Register maps
    for rm in info['regmaps']:
        lines.append(rm['map'].to_markdown(title=f"Register Map: {rm['name']}"))
        lines.append('')

    # Block diagram (mermaid)
    if inputs or outputs or info['submodules']:
        lines.append('## Block Diagram')
        lines.append('')
        lines.append('```mermaid')
        lines.append('graph LR')
        for p in inputs:
            lines.append(f"    {p['name']}([{p['name']}]) --> {name}")
        for p in outputs:
            lines.append(f"    {name} --> {p['name']}([{p['name']}])")
        for s in info['submodules']:
            lines.append(f"    {name} --- {s['name']}[{s['type']}]")
        lines.append('```')
        lines.append('')

    return '\n'.join(lines)
