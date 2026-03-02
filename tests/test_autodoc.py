"""Tests for auto-documentation generator."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest

from veripy import Module, Input, Output, Register, Signal, Mem, Interface, module, posedge
from veripy.context import comb, always, fsm
from veripy.autodoc import introspect, to_markdown


# ── Test fixtures ────────────────────────────────────────────────────

class SimpleCounter(Module):
    def __init__(self, width=8):
        self.clock = Input()
        self.reset = Input()
        self.enable = Input()
        self.count = Output(width)
        self.cnt = Register(width)
        super().__init__()

        @self.comb
        def drive():
            self.count = self.cnt

        @self.posedge(self.clock)
        def inc():
            if self.reset:
                self.cnt = 0
            elif self.enable:
                self.cnt = self.cnt + 1


class TestBus(Interface):
    data = ('output', 8)
    valid = ('output', 1)
    ready = ('input', 1)


class WithInterface(Module):
    def __init__(self):
        self.clock = Input()
        self.bus = TestBus()
        super().__init__()


class WithSubmodule(Module):
    def __init__(self):
        self.clock = Input()
        self.reset = Input()
        self.sub = SimpleCounter(width=4)
        super().__init__()


class WithMem(Module):
    def __init__(self):
        self.clock = Input()
        self.mem = Mem(16, 8)
        super().__init__()


@module
def fsm_mod():
    clock = Input()
    reset = Input()
    out = Output()

    @fsm(clock, reset, states=['IDLE', 'RUN', 'DONE'])
    def ctrl(state, IDLE, RUN, DONE):
        if state == IDLE:
            return RUN
        elif state == RUN:
            return DONE
        elif state == DONE:
            return IDLE


# ── introspect() tests ───────────────────────────────────────────────

class TestIntrospect(unittest.TestCase):

    def test_params(self):
        info = introspect(SimpleCounter(width=4))
        self.assertEqual(info['params']['width'], 4)

    def test_ports(self):
        info = introspect(SimpleCounter())
        names = {p['name'] for p in info['ports']}
        self.assertIn('clock', names)
        self.assertIn('count', names)
        inputs = [p for p in info['ports'] if p['kind'] == 'input']
        outputs = [p for p in info['ports'] if p['kind'] == 'output']
        self.assertEqual(len(inputs), 3)  # clock, reset, enable
        self.assertEqual(len(outputs), 1)  # count

    def test_port_width(self):
        info = introspect(SimpleCounter(width=16))
        count = next(p for p in info['ports'] if p['name'] == 'count')
        self.assertEqual(count['width'], 16)

    def test_registers(self):
        info = introspect(SimpleCounter())
        reg_names = {r['name'] for r in info['registers']}
        self.assertIn('cnt', reg_names)

    def test_submodules(self):
        info = introspect(WithSubmodule())
        self.assertEqual(len(info['submodules']), 1)
        self.assertEqual(info['submodules'][0]['name'], 'sub')
        self.assertEqual(info['submodules'][0]['type'], 'SimpleCounter')

    def test_interfaces(self):
        info = introspect(WithInterface())
        self.assertEqual(len(info['interfaces']), 1)
        self.assertEqual(info['interfaces'][0]['name'], 'bus')
        sig_names = {s['name'] for s in info['interfaces'][0]['signals']}
        self.assertIn('data', sig_names)
        self.assertIn('valid', sig_names)
        self.assertIn('ready', sig_names)

    def test_memories(self):
        info = introspect(WithMem())
        self.assertEqual(len(info['memories']), 1)
        self.assertEqual(info['memories'][0]['depth'], 16)
        self.assertEqual(info['memories'][0]['width'], 8)

    def test_fsm(self):
        info = introspect(fsm_mod())
        self.assertIsNotNone(info['fsm'])
        self.assertEqual(info['fsm']['states'], ['IDLE', 'RUN', 'DONE'])

    def test_no_fsm(self):
        info = introspect(SimpleCounter())
        self.assertIsNone(info['fsm'])


# ── to_markdown() tests ─────────────────────────────────────────────

class TestToMarkdown(unittest.TestCase):

    def test_header(self):
        md = to_markdown(SimpleCounter(), module_name='counter')
        self.assertTrue(md.startswith('# counter\n'))

    def test_params_section(self):
        md = to_markdown(SimpleCounter(width=4))
        self.assertIn('## Parameters', md)
        self.assertIn('| width | 4 |', md)

    def test_ports_table(self):
        md = to_markdown(SimpleCounter())
        self.assertIn('## Ports', md)
        self.assertIn('| clock | input | 1 |', md)
        self.assertIn('| count | output | 8 |', md)

    def test_registers_section(self):
        md = to_markdown(SimpleCounter())
        self.assertIn('## Registers', md)
        self.assertIn('| cnt | 8 |', md)

    def test_fsm_section(self):
        md = to_markdown(fsm_mod())
        self.assertIn('## FSM', md)
        self.assertIn('IDLE', md)
        self.assertIn('RUN', md)
        self.assertIn('DONE', md)

    def test_submodules_section(self):
        md = to_markdown(WithSubmodule())
        self.assertIn('## Sub-modules', md)
        self.assertIn('| sub | SimpleCounter |', md)

    def test_interfaces_section(self):
        md = to_markdown(WithInterface())
        self.assertIn('## Interfaces', md)
        self.assertIn('### bus', md)

    def test_memories_section(self):
        md = to_markdown(WithMem())
        self.assertIn('## Memories', md)
        self.assertIn('| mem | Mem | 16 | 8 |', md)

    def test_block_diagram(self):
        md = to_markdown(SimpleCounter())
        self.assertIn('```mermaid', md)
        self.assertIn('graph LR', md)

    def test_no_params_section_when_empty(self):
        """Module with no params should not have Parameters section."""
        md = to_markdown(WithMem())
        self.assertNotIn('## Parameters', md)


if __name__ == '__main__':
    unittest.main()
