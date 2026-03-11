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
    """Return a dict of design statistics for *module*, flattened across hierarchy."""
    from .lower import lower_module
    from .flatten import _expr_reads, _stmt_writes_reads
    from .signal import Signal

    mod_name = name or _to_snake(type(module).__name__)

    # Collect stats recursively across all sub-modules
    totals = dict(inputs=0, outputs=0, registers=0, reg_bits=0,
                  memories=0, mem_bits=0, instances=0, comb_blocks=0, seq_blocks=0)

    def _resolve(w, params):
        if isinstance(w, int): return w
        if isinstance(w, str): return params.get(w, 0)
        return 0

    def _accumulate(mod, is_top=False):
        ir = lower_module(mod, type(mod).__name__.lower())
        if is_top:
            totals['inputs'] = sum(1 for p in ir.ports if p.direction == 'input')
            totals['outputs'] = sum(1 for p in ir.ports if p.direction == 'output')
        totals['registers'] += len(ir.regs)
        totals['reg_bits'] += sum(_resolve(r.width, ir.params) for r in ir.regs)
        totals['memories'] += len(ir.mems)
        totals['mem_bits'] += sum(
            _resolve(m.depth, ir.params) * _resolve(m.width, ir.params)
            for m in ir.mems
        )
        totals['instances'] += len(ir.instances)
        totals['comb_blocks'] += len(ir.comb_blocks)
        totals['seq_blocks'] += len(ir.seq_blocks)
        # Recurse into sub-modules
        for k in dir(mod):
            v = getattr(mod, k, None)
            from .module import Module as _Module
            if isinstance(v, _Module) and v is not mod:
                _accumulate(v)

    _accumulate(module, is_top=True)

    # Flatten to count wire/comb signals
    from .flatten import flatten_ir
    from .backend_csim import _collect_submodule_registry, _build_sig_widths, _collect_nba_signals
    registry, patch_fn = _collect_submodule_registry(module)
    top_ir = lower_module(module, mod_name)
    patch_fn(top_ir)
    flat = flatten_ir(top_ir, registry) if top_ir.instances else top_ir
    sig_w = _build_sig_widths(flat)
    nba_sigs, _ = _collect_nba_signals(flat)
    _ports = {p.name for p in flat.ports}
    _regs = {r.name for r in flat.regs}
    _mems = {m.name for m in flat.mems}
    wire_sigs = {n for n in sig_w if n not in _ports and n not in _regs and n not in _mems and n not in nba_sigs}
    wire_bits = sum(sig_w[n] for n in wire_sigs)

    # Estimate comb depth on top-level IR only
    ir = lower_module(module, mod_name)
    nodes = []
    for b in ir.comb_blocks:
        w, r = set(), set()
        for s in b.stmts:
            _stmt_writes_reads(s, w, r)
        nodes.append((w, r))
    for a in ir.assigns:
        nodes.append(({a.target}, _expr_reads(a.value)))
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
        **totals,
        'wires': len(wire_sigs),
        'wire_bits': wire_bits,
        'comb_depth_est': comb_depth,
    }
