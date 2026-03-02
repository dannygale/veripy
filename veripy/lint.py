"""Lint / static checks for VeriPy modules."""

import ast
import inspect
import textwrap


def _get_func_ast(func):
    src = getattr(func, '_veripy_emit_source', None)
    if src is None:
        src = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(src)
    return tree.body[0] if isinstance(tree.body[0], ast.FunctionDef) else tree


def _is_self(node):
    return isinstance(node, ast.Name) and node.id == 'self'


def _self_attr_name(node):
    """Extract 'x' from self.x, or 'sub.port' from self.sub.port. Returns None otherwise."""
    if isinstance(node, ast.Attribute) and _is_self(node.value):
        return node.attr
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
            and _is_self(node.value.value)):
        return f'{node.value.attr}.{node.attr}'
    return None


def _collect_reads_writes(func):
    """Analyze a logic block's AST to find self.signal reads and writes."""
    tree = _get_func_ast(func)
    writes, reads = set(), set()

    for node in ast.walk(tree):
        # Writes: self.x = ... (Assign targets)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                name = _self_attr_name(t)
                if name:
                    writes.add(name.split('.')[0] if '.' in name else name)
                # Also check subscript: self.x[i] = ...
                if isinstance(t, ast.Subscript):
                    name = _self_attr_name(t.value)
                    if name:
                        writes.add(name.split('.')[0] if '.' in name else name)

        # Writes: self.mem.write(addr, data)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'write'):
            name = _self_attr_name(node.func.value)
            if name:
                writes.add(name)

    # Reads: any self.x reference NOT as an assignment target
    class _ReadVisitor(ast.NodeVisitor):
        def visit_Attribute(self, node):
            name = _self_attr_name(node)
            if name:
                reads.add(name.split('.')[0] if '.' in name else name)
            self.generic_visit(node)

    _ReadVisitor().visit(tree)
    # Remove writes from reads (a signal can be both read and written)
    return reads, writes


def _any_assigned(stmts):
    """Return set of self.x names assigned on ANY path through *stmts*."""
    result = set()
    for stmt in stmts:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                name = _self_attr_name(t)
                if name:
                    result.add(name)
        elif isinstance(stmt, ast.If):
            result |= _any_assigned(stmt.body)
            result |= _any_assigned(stmt.orelse)
    return result


def _always_assigned(stmts):
    """Return set of self.x names assigned on ALL paths through *stmts*."""
    result = set()
    for stmt in stmts:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                name = _self_attr_name(t)
                if name:
                    result.add(name)
        elif isinstance(stmt, ast.If):
            if_assigns = _always_assigned(stmt.body)
            if stmt.orelse:
                else_assigns = _always_assigned(stmt.orelse)
                result |= (if_assigns & else_assigns)
            # No else → nothing from the if branch is guaranteed
    return result


def lint(module):
    """Run all lint checks on a Module. Returns list of (level, message) tuples."""
    from .signal import Signal, Mem
    from .module import Module

    warnings = []

    # Collect all signal names
    signals = {}
    for k in dir(module):
        v = getattr(module, k)
        if isinstance(v, Signal):
            signals[k] = v
        elif isinstance(v, Mem):
            signals[k] = v

    outputs = {k for k, v in signals.items() if isinstance(v, Signal) and v._kind == 'output'}
    registers = {k for k, v in signals.items() if isinstance(v, Signal) and v._kind == 'reg'}

    # Collect reads/writes across all blocks
    all_writes = {}  # signal_name → list of block descriptions
    all_reads = set()

    for method in module._comb_blocks:
        reads, writes = _collect_reads_writes(method)
        all_reads |= reads
        for w in writes:
            all_writes.setdefault(w, []).append(f'@comb {method.__name__}')

    for edges, method in module._always_blocks:
        reads, writes = _collect_reads_writes(method)
        all_reads |= reads
        for w in writes:
            all_writes.setdefault(w, []).append(f'@always {method.__name__}')

    # Check 1: Undriven outputs
    for name in sorted(outputs):
        if name not in all_writes:
            warnings.append(('warning', f"output '{name}' is never driven"))

    # Check 2: Multi-driven signals
    for name, drivers in sorted(all_writes.items()):
        if len(drivers) > 1:
            drivers_str = ', '.join(drivers)
            warnings.append(('warning', f"signal '{name}' driven by multiple blocks: {drivers_str}"))

    # Check 3: Missing reset for registers in posedge blocks
    for edges, method in module._always_blocks:
        is_posedge = any(e.kind == 'posedge' for e in edges)
        if not is_posedge:
            continue
        _reads, writes = _collect_reads_writes(method)
        tree = _get_func_ast(method)
        # Check if there's an if/reset pattern
        has_reset = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                cond_name = _self_attr_name(node.test)
                if cond_name and 'reset' in cond_name.lower():
                    has_reset = True
                    break
                # Also check `not self.rst_n` pattern
                if isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not):
                    cond_name = _self_attr_name(node.test.operand)
                    if cond_name and ('rst' in cond_name.lower() or 'reset' in cond_name.lower()):
                        has_reset = True
                        break
        reg_writes = writes & registers
        if reg_writes and not has_reset:
            for r in sorted(reg_writes):
                warnings.append(('info', f"register '{r}' in {method.__name__} has no reset path"))

    # Check 4: Clock domain crossing
    # Map each posedge block's clock, track which regs it writes
    clocks = set()
    reg_clock = {}  # register_name → clock_signal_name
    block_clock = {}  # block index → clock_signal_name
    block_reads = {}  # block index → set of read signal names
    for i, (edges, method) in enumerate(module._always_blocks):
        posedge_clocks = [e.signal.name for e in edges if e.kind == 'posedge']
        if not posedge_clocks:
            continue
        clk = posedge_clocks[0]
        clocks.add(clk)
        block_clock[i] = clk
        reads, writes = _collect_reads_writes(method)
        block_reads[i] = reads
        for w in writes:
            if w in registers:
                reg_clock[w] = clk

    # Build reverse map: clock_signal_name → domain_name (if declared)
    domains = getattr(module, '_clock_domains', {})
    clk_to_domain = {clk: name for name, clk in domains.items()}

    if len(clocks) > 1:
        for i, clk in block_clock.items():
            for r in block_reads[i]:
                if r in reg_clock and reg_clock[r] != clk:
                    src_clk = reg_clock[r]
                    src_dom = clk_to_domain.get(src_clk)
                    dst_dom = clk_to_domain.get(clk)
                    if src_dom and dst_dom:
                        warnings.append(('warning',
                            f"CDC: register '{r}' (domain '{src_dom}') "
                            f"read in domain '{dst_dom}'"))
                    else:
                        warnings.append(('warning',
                            f"CDC: register '{r}' (clocked by {src_clk}) "
                            f"read in block clocked by {clk}"))

    # Check 5: Unused signals (declared but never read)
    submodules = set()
    for k in dir(module):
        if not k.startswith('_'):
            v = getattr(module, k)
            if isinstance(v, Module) and v is not module:
                submodules.add(k)

    internal = {k for k, v in signals.items()
                if isinstance(v, Signal) and v._kind not in ('input', 'output')}
    for name in sorted(internal):
        if name not in all_reads and name not in submodules:
            warnings.append(('info', f"signal '{name}' is declared but never read"))

    # Check 6: Latch inference in @comb blocks
    for method in module._comb_blocks:
        tree = _get_func_ast(method)
        body = tree.body
        any_set = _any_assigned(body)
        always_set = _always_assigned(body)
        latched = sorted(any_set - always_set)
        for name in latched:
            warnings.append(('warning',
                f"signal '{name}' not assigned on all paths in @comb "
                f"{method.__name__} (latch inferred)"))

    # Check 7: Combinational loop detection
    # Build dependency graph across @comb blocks: read → write edges
    comb_graph = {}  # signal → set of signals it feeds into
    for method in module._comb_blocks:
        reads, writes = _collect_reads_writes(method)
        for r in reads:
            if r in signals:
                for w in writes:
                    if w in signals and w != r:
                        comb_graph.setdefault(r, set()).add(w)

    # DFS cycle detection
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {s: WHITE for s in comb_graph}
    path = []

    def _dfs(node):
        color[node] = GRAY
        path.append(node)
        for nxt in comb_graph.get(node, ()):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                cycle_start = path.index(nxt)
                return path[cycle_start:]
            if color[nxt] == WHITE:
                result = _dfs(nxt)
                if result:
                    return result
        path.pop()
        color[node] = BLACK
        return None

    reported = set()
    for node in list(comb_graph):
        if color.get(node, WHITE) == WHITE:
            cycle = _dfs(node)
            if cycle:
                key = tuple(sorted(cycle))
                if key not in reported:
                    reported.add(key)
                    loop_str = ' → '.join(cycle + [cycle[0]])
                    warnings.append(('error',
                        f"combinational loop detected: {loop_str}"))

    return warnings
