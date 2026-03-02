"""CSR / register map generator.

Define register maps with field-level granularity, then generate RTL
(via :class:`Axi4LiteSub`), C headers, and documentation from a single
source of truth.

Usage::

    from veripy.csr import Field, Reg, RegisterMap

    rmap = RegisterMap([
        Reg('ctrl', 0x00, [
            Field('enable', bits=0, access='rw', reset=0),
            Field('mode',   bits=(2, 1), access='rw', reset=0),
        ]),
        Reg('status', 0x04, [
            Field('busy',  bits=0, access='ro'),
            Field('error', bits=1, access='ro'),
        ]),
    ])

    mod = rmap.to_module()
    hdr = rmap.to_c_header('MY_IP')
"""


class Field:
    """A named bit field within a CSR register.

    Parameters
    ----------
    name : str
        Field name (used in C macros and documentation).
    bits : int or tuple(int, int)
        Single bit index, or ``(msb, lsb)`` range inclusive.
    access : str
        ``'rw'``, ``'ro'``, or ``'wo'``.
    reset : int
        Reset value (default 0).
    desc : str
        Optional description.
    """

    def __init__(self, name, bits, access='rw', reset=0, desc=''):
        self.name = name
        if isinstance(bits, int):
            self.msb = self.lsb = bits
        else:
            self.msb, self.lsb = bits
        if self.msb < self.lsb:
            raise ValueError(f"Field '{name}': msb ({self.msb}) < lsb ({self.lsb})")
        self.access = access
        self.reset = reset
        self.desc = desc

    @property
    def width(self):
        return self.msb - self.lsb + 1

    @property
    def mask(self):
        return ((1 << self.width) - 1) << self.lsb


class Reg:
    """A CSR register at a given byte offset.

    Parameters
    ----------
    name : str
        Register name.
    offset : int
        Byte address offset.
    fields : list of Field
        Bit fields within this register.
    desc : str
        Optional description.
    """

    def __init__(self, name, offset, fields=None, desc=''):
        self.name = name
        self.offset = offset
        self.fields = fields or []
        self.desc = desc
        self._validate()

    def _validate(self):
        """Check for overlapping fields."""
        used = 0
        for f in self.fields:
            if used & f.mask:
                raise ValueError(
                    f"Reg '{self.name}': field '{f.name}' overlaps previous fields")
            used |= f.mask

    @property
    def access(self):
        """Derive register-level access from fields."""
        accesses = {f.access for f in self.fields}
        if accesses == {'ro'}:
            return 'ro'
        if accesses == {'wo'}:
            return 'wo'
        return 'rw'

    @property
    def reset_val(self):
        """Combined reset value from all fields."""
        val = 0
        for f in self.fields:
            val |= (f.reset & ((1 << f.width) - 1)) << f.lsb
        return val


class RegisterMap:
    """A collection of CSR registers.

    Parameters
    ----------
    regs : list of Reg
        Register definitions.
    data_width : int
        Bus data width in bits (default 32).
    addr_width : int
        Bus address width in bits (default 32).
    """

    def __init__(self, regs, data_width=32, addr_width=32):
        self.regs = regs
        self.data_width = data_width
        self.addr_width = addr_width
        self._validate()

    def _validate(self):
        offsets = {}
        for r in self.regs:
            if r.offset in offsets:
                raise ValueError(
                    f"Duplicate offset 0x{r.offset:X}: "
                    f"'{offsets[r.offset]}' and '{r.name}'")
            offsets[r.offset] = r.name

    def to_module(self):
        """Generate an :class:`Axi4LiteSub` module from this register map."""
        from .axi4lite import Axi4LiteSub
        reg_map = [
            (r.offset, r.name, self.data_width, r.access)
            for r in self.regs
        ]
        return Axi4LiteSub(reg_map=reg_map,
                            data_width=self.data_width,
                            addr_width=self.addr_width)

    def to_c_header(self, prefix='CSR'):
        """Generate a C header string with register and field definitions."""
        p = prefix.upper()
        lines = [
            f'#ifndef _{p}_REGS_H',
            f'#define _{p}_REGS_H',
            '',
            '#include <stdint.h>',
            '',
        ]
        for r in self.regs:
            rn = r.name.upper()
            lines.append(f'#define {p}_{rn}_OFFSET 0x{r.offset:04X}')
            lines.append(f'#define {p}_{rn}_RESET  0x{r.reset_val:08X}')
            for f in r.fields:
                fn = f.name.upper()
                lines.append(f'#define {p}_{rn}_{fn}_SHIFT {f.lsb}')
                lines.append(f'#define {p}_{rn}_{fn}_MASK  0x{f.mask:08X}')
            lines.append('')
        lines.append(f'#endif /* _{p}_REGS_H */')
        lines.append('')
        return '\n'.join(lines)

    def to_python_driver(self, class_name='RegDriver'):
        """Generate a Python driver class for register-level read/write access.

        The generated class expects a *bus* object with ``read(addr)`` and
        ``write(addr, data)`` methods (e.g. an AXI-Lite transaction helper).
        """
        ind = '    '
        lines = [
            f'class {class_name}:',
        ]
        # ── offset / mask / shift constants ──
        for r in self.regs:
            rn = r.name.upper()
            lines.append(f'{ind}{rn}_OFFSET = 0x{r.offset:04X}')
            for f in r.fields:
                fn = f.name.upper()
                lines.append(f'{ind}{rn}_{fn}_SHIFT = {f.lsb}')
                lines.append(f'{ind}{rn}_{fn}_MASK = 0x{f.mask:08X}')
        lines.append('')
        # ── __init__ ──
        lines.append(f'{ind}def __init__(self, bus):')
        lines.append(f'{ind}{ind}self.bus = bus')
        lines.append('')
        # ── per-register read/write ──
        for r in self.regs:
            rn = r.name
            RN = rn.upper()
            if r.access != 'wo':
                lines.append(f'{ind}def read_{rn}(self):')
                lines.append(f'{ind}{ind}return self.bus.read(self.{RN}_OFFSET)')
                lines.append('')
            if r.access != 'ro':
                lines.append(f'{ind}def write_{rn}(self, val):')
                lines.append(f'{ind}{ind}self.bus.write(self.{RN}_OFFSET, val)')
                lines.append('')
            # ── per-field get/set ──
            for f in r.fields:
                fn = f.name
                FN = fn.upper()
                if f.access != 'wo':
                    lines.append(f'{ind}def get_{rn}_{fn}(self):')
                    lines.append(f'{ind}{ind}return (self.read_{rn}() >> self.{RN}_{FN}_SHIFT) & 0x{(1 << f.width) - 1:X}')
                    lines.append('')
                if f.access != 'ro':
                    lines.append(f'{ind}def set_{rn}_{fn}(self, val):')
                    lines.append(f'{ind}{ind}cur = self.read_{rn}()')
                    lines.append(f'{ind}{ind}cur = (cur & ~self.{RN}_{FN}_MASK) | ((val << self.{RN}_{FN}_SHIFT) & self.{RN}_{FN}_MASK)')
                    lines.append(f'{ind}{ind}self.write_{rn}(cur)')
                    lines.append('')
        return '\n'.join(lines)

    def to_markdown(self, title='Register Map'):
        """Generate Markdown documentation for this register map."""
        lines = [f'# {title}', '']
        for r in self.regs:
            lines.append(f'## {r.name} (0x{r.offset:04X})')
            if r.desc:
                lines.append(f'\n{r.desc}')
            lines.append(f'\nAccess: {r.access} | Reset: 0x{r.reset_val:08X}')
            if r.fields:
                lines.append('')
                lines.append('| Bits | Field | Access | Reset | Description |')
                lines.append('|------|-------|--------|-------|-------------|')
                for f in r.fields:
                    bits = str(f.lsb) if f.msb == f.lsb else f'{f.msb}:{f.lsb}'
                    lines.append(f'| {bits} | {f.name} | {f.access} | 0x{f.reset:X} | {f.desc} |')
            lines.append('')
        return '\n'.join(lines)
