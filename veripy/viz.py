"""Hierarchy visualization and design statistics for VeriPy modules."""

from .emit_verilog import _to_snake


def module_graph_dot(module, name=None):
    """Return a DOT string representing the module hierarchy rooted at *module*."""
    top_name = name or _to_snake(type(module).__name__)
    lines = ['digraph hierarchy {', '    rankdir=LR;',
             '    node [shape=box fontname=monospace];']
    _dot_walk(module, top_name, set(), lines)
    lines.append('}')
    return '\n'.join(lines)


def _dot_walk(module, mod_name, seen, lines):
    subs = module._submodules()
    if mod_name not in seen:
        seen.add(mod_name)
        n_in = sum(1 for s in module._signals().values() if s._kind == 'input')
        n_out = sum(1 for s in module._signals().values() if s._kind == 'output')
        label = f'{mod_name}\\nin:{n_in} out:{n_out}'
        lines.append(f'    "{mod_name}" [label="{label}"];')
    for inst_name, sub in subs.items():
        sub_type = _to_snake(type(sub).__name__)
        sub_node = f'{mod_name}.{inst_name}'
        sub_in = sum(1 for s in sub._signals().values() if s._kind == 'input')
        sub_out = sum(1 for s in sub._signals().values() if s._kind == 'output')
        sub_label = f'{inst_name}\\n({sub_type})\\nin:{sub_in} out:{sub_out}'
        if sub_node not in seen:
            seen.add(sub_node)
            lines.append(f'    "{sub_node}" [label="{sub_label}"];')
        lines.append(f'    "{mod_name}" -> "{sub_node}";')
        _dot_walk(sub, sub_node, seen, lines)


def module_stats(module, name=None):
    """Return a dict of design statistics for *module* (lowered to IR)."""
    from .lower import lower_module
    from .flatten import _expr_reads, _stmt_writes_reads

    mod_name = name or _to_snake(type(module).__name__)
    ir = lower_module(module, mod_name)

    n_inputs = sum(1 for p in ir.ports if p.direction == 'input')
    n_outputs = sum(1 for p in ir.ports if p.direction == 'output')

    def _resolve(w):
        if isinstance(w, int):
            return w
        return ir.params.get(w, 0) if isinstance(w, str) else 0

    reg_bits = sum(_resolve(r.width) for r in ir.regs)
    mem_bits = sum(m.depth * _resolve(m.width) for m in ir.mems)

    # Estimate comb depth: longest dependency chain in comb blocks
    nodes = []
    for b in ir.comb_blocks:
        w, r = set(), set()
        for s in b.stmts:
            _stmt_writes_reads(s, w, r)
        nodes.append((w, r))
    for a in ir.assigns:
        nodes.append(({a.target}, _expr_reads(a.value)))

    # Build adjacency: node i depends on node j if j writes something i reads
    n = len(nodes)
    depth = [1] * n
    for i in range(n):
        _, r_i = nodes[i]
        for j in range(i):
            w_j, _ = nodes[j]
            if w_j & r_i:
                depth[i] = max(depth[i], depth[j] + 1)
    comb_depth = max(depth) if depth else 0

    return {
        'name': mod_name,
        'inputs': n_inputs,
        'outputs': n_outputs,
        'registers': len(ir.regs),
        'reg_bits': reg_bits,
        'memories': len(ir.mems),
        'mem_bits': mem_bits,
        'instances': len(ir.instances),
        'comb_blocks': len(ir.comb_blocks),
        'seq_blocks': len(ir.seq_blocks),
        'comb_depth_est': comb_depth,
    }
