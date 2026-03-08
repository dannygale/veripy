"""Declarative SoC builder from YAML/JSON config.

Parses a SoC description file, validates it, generates an address map,
AXI4-Lite interconnect, top-level Verilog, C headers, and linker scripts.

Schema (YAML or JSON)::

    soc:
      name: my_soc

    cpu:                        # optional
      type: blackbox
      file: cpu.v               # optional Verilog file to include
      clock: clk
      reset: rst
      bus_prefix: dbus          # AXI4-Lite master port prefix

    bus:                        # optional, defaults shown
      data_width: 32
      addr_width: 32

    memory:
      - name: rom
        base: 0x00000000
        size: 0x10000
        type: rom               # rom | ram | flash
      - name: ram
        base: 0x20000000
        size: 0x8000
        type: ram

    peripherals:                # optional
      - name: uart0
        base: 0x40000000
        size: 0x100
        type: blackbox
        file: uart.v            # optional

    platform:
      clock: clk
      reset: rst
      reset_active: high        # high | low
"""

import json
import os
import sys
from dataclasses import dataclass, field


# ── YAML support (optional) ────────────────────────────────────────────────────

def _load_file(path: str) -> dict:
    """Load YAML or JSON file. Tries PyYAML first, falls back to JSON."""
    with open(path) as f:
        text = f.read()
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.yaml', '.yml'):
        try:
            import yaml  # type: ignore[import]
            return yaml.safe_load(text)
        except ImportError:
            sys.exit(
                "error: PyYAML is required for .yaml files: pip install pyyaml"
            )
    return json.loads(text)


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class MemoryRegion:
    name: str
    base: int
    size: int
    type: str  # rom | ram | flash


@dataclass
class Peripheral:
    name: str
    base: int
    size: int
    type: str  # blackbox
    file: str = ''


@dataclass
class CpuConfig:
    type: str = 'blackbox'
    file: str = ''
    clock: str = 'clk'
    reset: str = 'rst'
    bus_prefix: str = 'dbus'


@dataclass
class BusConfig:
    data_width: int = 32
    addr_width: int = 32


@dataclass
class PlatformConfig:
    clock: str = 'clk'
    reset: str = 'rst'
    reset_active: str = 'high'


@dataclass
class SocConfig:
    name: str
    cpu: CpuConfig | None
    bus: BusConfig
    memory: list[MemoryRegion]
    peripherals: list[Peripheral]
    platform: PlatformConfig


# ── Parser / validator ─────────────────────────────────────────────────────────

def _parse_int(val) -> int:
    """Accept int or hex string like '0x1000'."""
    if isinstance(val, int):
        return val
    return int(val, 0)


def parse_soc_config(path: str) -> SocConfig:
    """Load and validate a SoC config file. Raises ValueError on errors."""
    raw = _load_file(path)
    errors = []

    soc_sec = raw.get('soc', {})
    name = soc_sec.get('name', '')
    if not name:
        errors.append("soc.name is required")

    # cpu (optional)
    cpu = None
    if 'cpu' in raw:
        c = raw['cpu']
        cpu = CpuConfig(
            type=c.get('type', 'blackbox'),
            file=c.get('file', ''),
            clock=c.get('clock', 'clk'),
            reset=c.get('reset', 'rst'),
            bus_prefix=c.get('bus_prefix', 'dbus'),
        )
        if cpu.type != 'blackbox':
            errors.append(f"cpu.type '{cpu.type}' not supported (only 'blackbox')")

    # bus (optional)
    b = raw.get('bus', {})
    bus = BusConfig(
        data_width=b.get('data_width', 32),
        addr_width=b.get('addr_width', 32),
    )

    # memory (required)
    memory = []
    for i, m in enumerate(raw.get('memory', [])):
        if 'name' not in m:
            errors.append(f"memory[{i}]: 'name' is required")
            continue
        if 'base' not in m:
            errors.append(f"memory[{i}] '{m['name']}': 'base' is required")
            continue
        if 'size' not in m:
            errors.append(f"memory[{i}] '{m['name']}': 'size' is required")
            continue
        mtype = m.get('type', 'ram')
        if mtype not in ('rom', 'ram', 'flash'):
            errors.append(f"memory '{m['name']}': type must be rom|ram|flash")
        memory.append(MemoryRegion(
            name=m['name'],
            base=_parse_int(m['base']),
            size=_parse_int(m['size']),
            type=mtype,
        ))
    if not memory:
        errors.append("at least one memory region is required")

    # peripherals (optional)
    peripherals = []
    for i, p in enumerate(raw.get('peripherals', [])):
        if 'name' not in p:
            errors.append(f"peripherals[{i}]: 'name' is required")
            continue
        if 'base' not in p:
            errors.append(f"peripherals[{i}] '{p['name']}': 'base' is required")
            continue
        if 'size' not in p:
            errors.append(f"peripherals[{i}] '{p['name']}': 'size' is required")
            continue
        peripherals.append(Peripheral(
            name=p['name'],
            base=_parse_int(p['base']),
            size=_parse_int(p['size']),
            type=p.get('type', 'blackbox'),
            file=p.get('file', ''),
        ))

    # platform (required)
    plat_raw = raw.get('platform', {})
    if not plat_raw.get('clock'):
        errors.append("platform.clock is required")
    if not plat_raw.get('reset'):
        errors.append("platform.reset is required")
    reset_active = plat_raw.get('reset_active', 'high')
    if reset_active not in ('high', 'low'):
        errors.append("platform.reset_active must be 'high' or 'low'")
    platform = PlatformConfig(
        clock=plat_raw.get('clock', 'clk'),
        reset=plat_raw.get('reset', 'rst'),
        reset_active=reset_active,
    )

    if errors:
        raise ValueError("SoC config errors:\n" + "\n".join(f"  {e}" for e in errors))

    return SocConfig(
        name=name,
        cpu=cpu,
        bus=bus,
        memory=memory,
        peripherals=peripherals,
        platform=platform,
    )


# ── Address map ────────────────────────────────────────────────────────────────

@dataclass
class AddrEntry:
    name: str
    base: int
    size: int
    kind: str  # 'memory' or 'peripheral'
    subtype: str  # rom/ram/flash/blackbox


class AddressMap:
    """Validated address map with overlap detection.

    Parameters
    ----------
    config : SocConfig
    """

    def __init__(self, config: SocConfig):
        entries: list[AddrEntry] = []
        for m in config.memory:
            entries.append(AddrEntry(m.name, m.base, m.size, 'memory', m.type))
        for p in config.peripherals:
            entries.append(AddrEntry(p.name, p.base, p.size, 'peripheral', p.type))

        # Sort by base address
        entries.sort(key=lambda e: e.base)

        # Overlap detection
        for i in range(len(entries) - 1):
            a, b = entries[i], entries[i + 1]
            if a.base + a.size > b.base:
                raise ValueError(
                    f"Address overlap: '{a.name}' "
                    f"[0x{a.base:08X}..0x{a.base + a.size - 1:08X}] overlaps "
                    f"'{b.name}' [0x{b.base:08X}..0x{b.base + b.size - 1:08X}]"
                )

        self.entries = entries
        self.config = config

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)


# ── Interconnect generator ─────────────────────────────────────────────────────

def _axi_ports(prefix: str, dw: int, aw: int, is_master: bool) -> list[str]:
    """Return Verilog port declarations for one AXI4-Lite interface."""
    sw = dw // 8
    inp = 'input  wire' if is_master else 'output wire'
    out = 'output wire' if is_master else 'input  wire'
    return [
        f'    {inp} [{aw}-1:0] {prefix}_awaddr,',
        f'    {inp}            {prefix}_awvalid,',
        f'    {out}            {prefix}_awready,',
        f'    {inp} [2:0]      {prefix}_awprot,',
        f'    {inp} [{dw}-1:0] {prefix}_wdata,',
        f'    {inp} [{sw}-1:0] {prefix}_wstrb,',
        f'    {inp}            {prefix}_wvalid,',
        f'    {out}            {prefix}_wready,',
        f'    {out} [1:0]      {prefix}_bresp,',
        f'    {out}            {prefix}_bvalid,',
        f'    {inp}            {prefix}_bready,',
        f'    {inp} [{aw}-1:0] {prefix}_araddr,',
        f'    {inp}            {prefix}_arvalid,',
        f'    {out}            {prefix}_arready,',
        f'    {inp} [2:0]      {prefix}_arprot,',
        f'    {out} [{dw}-1:0] {prefix}_rdata,',
        f'    {out} [1:0]      {prefix}_rresp,',
        f'    {out}            {prefix}_rvalid,',
        f'    {inp}            {prefix}_rready,',
    ]


def build_interconnect(config: SocConfig, addr_map: AddressMap) -> str:
    """Generate AXI4-Lite 1-master N-subordinate address decoder Verilog."""
    dw = config.bus.data_width
    aw = config.bus.addr_width
    n = len(addr_map)
    sel_bits = max(n.bit_length(), 1)
    clk = config.platform.clock
    rst = config.platform.reset
    rst_active_low = config.platform.reset_active == 'low'
    rst_cond = f'!{rst}' if rst_active_low else rst

    lines = [
        f'// Auto-generated AXI4-Lite interconnect for {config.name}',
        f'module {config.name}_interconnect #(',
        f'    parameter DATA_WIDTH = {dw},',
        f'    parameter ADDR_WIDTH = {aw}',
        f') (',
        f'    input wire {clk},',
        f'    input wire {rst},',
    ]

    # Master port (from CPU)
    if config.cpu:
        pfx = config.cpu.bus_prefix
        lines += _axi_ports(pfx, dw, aw, is_master=True)

    # Subordinate ports
    for e in addr_map:
        lines += _axi_ports(e.name, dw, aw, is_master=False)

    # Remove trailing comma from last port
    lines[-1] = lines[-1].rstrip(',')
    lines.append(');')
    lines.append('')

    if not config.cpu:
        lines.append('endmodule')
        return '\n'.join(lines)

    pfx = config.cpu.bus_prefix
    names = [e.name for e in addr_map]

    # sel register
    lines += [
        f'    // Address decode',
        f'    reg [{sel_bits}-1:0] wr_sel, rd_sel;',
        f'    localparam SEL_NONE = {n};',
        '',
        f'    always @(*) begin',
        f'        wr_sel = SEL_NONE;',
    ]
    for i, e in enumerate(addr_map):
        lines.append(
            f'        if ({pfx}_awaddr >= {aw}\'h{e.base:08X} && '
            f'{pfx}_awaddr < {aw}\'h{e.base + e.size:08X}) wr_sel = {i};'
        )
    lines += [
        f'    end',
        '',
        f'    always @(*) begin',
        f'        rd_sel = SEL_NONE;',
    ]
    for i, e in enumerate(addr_map):
        lines.append(
            f'        if ({pfx}_araddr >= {aw}\'h{e.base:08X} && '
            f'{pfx}_araddr < {aw}\'h{e.base + e.size:08X}) rd_sel = {i};'
        )
    lines.append(f'    end')
    lines.append('')

    # Route write address channel to subordinates
    for i, nm in enumerate(names):
        lines.append(f'    assign {nm}_awaddr  = {pfx}_awaddr;')
        lines.append(f'    assign {nm}_awprot  = {pfx}_awprot;')
        lines.append(f'    assign {nm}_awvalid = {pfx}_awvalid && (wr_sel == {i});')
        lines.append(f'    assign {nm}_wdata   = {pfx}_wdata;')
        lines.append(f'    assign {nm}_wstrb   = {pfx}_wstrb;')
        lines.append(f'    assign {nm}_wvalid  = {pfx}_wvalid && (wr_sel == {i});')
        lines.append(f'    assign {nm}_bready  = {pfx}_bready;')
        lines.append('')

    # Route read address channel to subordinates
    for i, nm in enumerate(names):
        lines.append(f'    assign {nm}_araddr  = {pfx}_araddr;')
        lines.append(f'    assign {nm}_arprot  = {pfx}_arprot;')
        lines.append(f'    assign {nm}_arvalid = {pfx}_arvalid && (rd_sel == {i});')
        lines.append(f'    assign {nm}_rready  = {pfx}_rready;')
        lines.append('')

    # Mux responses back to master
    def _mux(sig, default='0'):
        cases = ' : '.join(
            f'(wr_sel == {i}) ? {nm}_{sig}' for i, nm in enumerate(names)
        )
        return f'{cases} : {default}'

    def _mux_rd(sig, default='0'):
        cases = ' : '.join(
            f'(rd_sel == {i}) ? {nm}_{sig}' for i, nm in enumerate(names)
        )
        return f'{cases} : {default}'

    lines += [
        f'    assign {pfx}_awready = {_mux("awready")};',
        f'    assign {pfx}_wready  = {_mux("wready")};',
        f'    assign {pfx}_bresp   = {_mux("bresp", "2\'b11")};',
        f'    assign {pfx}_bvalid  = {_mux("bvalid")};',
        f'    assign {pfx}_arready = {_mux_rd("arready")};',
        f'    assign {pfx}_rdata   = {_mux_rd("rdata", "{" + str(dw) + "{1\'b0}}")};',
        f'    assign {pfx}_rresp   = {_mux_rd("rresp", "2\'b11")};',
        f'    assign {pfx}_rvalid  = {_mux_rd("rvalid")};',
        '',
        'endmodule',
    ]

    return '\n'.join(lines)


# ── Top-level module assembly ──────────────────────────────────────────────────

def build_top(config: SocConfig, addr_map: AddressMap) -> str:
    """Generate top-level Verilog that instantiates CPU, memories, peripherals, interconnect."""
    dw = config.bus.data_width
    aw = config.bus.addr_width
    sw = dw // 8
    clk = config.platform.clock
    rst = config.platform.reset
    sn = config.name

    lines = [
        f'// Auto-generated top-level for {sn}',
        f'module {sn} (',
        f'    input wire {clk},',
        f'    input wire {rst}',
        f');',
        '',
    ]

    # Declare AXI4-Lite wires for each subordinate
    for e in addr_map:
        nm = e.name
        lines += [
            f'    // {nm} AXI4-Lite wires',
            f'    wire [{aw}-1:0] {nm}_awaddr;',
            f'    wire            {nm}_awvalid;',
            f'    wire            {nm}_awready;',
            f'    wire [2:0]      {nm}_awprot;',
            f'    wire [{dw}-1:0] {nm}_wdata;',
            f'    wire [{sw}-1:0] {nm}_wstrb;',
            f'    wire            {nm}_wvalid;',
            f'    wire            {nm}_wready;',
            f'    wire [1:0]      {nm}_bresp;',
            f'    wire            {nm}_bvalid;',
            f'    wire            {nm}_bready;',
            f'    wire [{aw}-1:0] {nm}_araddr;',
            f'    wire            {nm}_arvalid;',
            f'    wire            {nm}_arready;',
            f'    wire [2:0]      {nm}_arprot;',
            f'    wire [{dw}-1:0] {nm}_rdata;',
            f'    wire [1:0]      {nm}_rresp;',
            f'    wire            {nm}_rvalid;',
            f'    wire            {nm}_rready;',
            '',
        ]

    # CPU master wires
    if config.cpu:
        pfx = config.cpu.bus_prefix
        lines += [
            f'    // CPU data bus wires',
            f'    wire [{aw}-1:0] {pfx}_awaddr;',
            f'    wire            {pfx}_awvalid;',
            f'    wire            {pfx}_awready;',
            f'    wire [2:0]      {pfx}_awprot;',
            f'    wire [{dw}-1:0] {pfx}_wdata;',
            f'    wire [{sw}-1:0] {pfx}_wstrb;',
            f'    wire            {pfx}_wvalid;',
            f'    wire            {pfx}_wready;',
            f'    wire [1:0]      {pfx}_bresp;',
            f'    wire            {pfx}_bvalid;',
            f'    wire            {pfx}_bready;',
            f'    wire [{aw}-1:0] {pfx}_araddr;',
            f'    wire            {pfx}_arvalid;',
            f'    wire            {pfx}_arready;',
            f'    wire [2:0]      {pfx}_arprot;',
            f'    wire [{dw}-1:0] {pfx}_rdata;',
            f'    wire [1:0]      {pfx}_rresp;',
            f'    wire            {pfx}_rvalid;',
            f'    wire            {pfx}_rready;',
            '',
        ]

        # CPU instantiation
        lines += [
            f'    // CPU',
            f'    {config.cpu.type if config.cpu.type != "blackbox" else "cpu"} cpu_inst (',
            f'        .{config.cpu.clock}({clk}),',
            f'        .{config.cpu.reset}({rst}),',
        ]
        for sig in ('awaddr', 'awvalid', 'awready', 'awprot',
                    'wdata', 'wstrb', 'wvalid', 'wready',
                    'bresp', 'bvalid', 'bready',
                    'araddr', 'arvalid', 'arready', 'arprot',
                    'rdata', 'rresp', 'rvalid', 'rready'):
            lines.append(f'        .{pfx}_{sig}({pfx}_{sig}),')
        lines[-1] = lines[-1].rstrip(',')
        lines += ['    );', '']

    # Interconnect instantiation
    lines += [
        f'    // Interconnect',
        f'    {sn}_interconnect #(',
        f'        .DATA_WIDTH({dw}),',
        f'        .ADDR_WIDTH({aw})',
        f'    ) interconnect_inst (',
        f'        .{clk}({clk}),',
        f'        .{rst}({rst}),',
    ]
    if config.cpu:
        pfx = config.cpu.bus_prefix
        for sig in ('awaddr', 'awvalid', 'awready', 'awprot',
                    'wdata', 'wstrb', 'wvalid', 'wready',
                    'bresp', 'bvalid', 'bready',
                    'araddr', 'arvalid', 'arready', 'arprot',
                    'rdata', 'rresp', 'rvalid', 'rready'):
            lines.append(f'        .{pfx}_{sig}({pfx}_{sig}),')
    for e in addr_map:
        nm = e.name
        for sig in ('awaddr', 'awvalid', 'awready', 'awprot',
                    'wdata', 'wstrb', 'wvalid', 'wready',
                    'bresp', 'bvalid', 'bready',
                    'araddr', 'arvalid', 'arready', 'arprot',
                    'rdata', 'rresp', 'rvalid', 'rready'):
            lines.append(f'        .{nm}_{sig}({nm}_{sig}),')
    lines[-1] = lines[-1].rstrip(',')
    lines += ['    );', '']

    # Memory/peripheral instantiations
    for e in addr_map:
        nm = e.name
        mod_name = f'{nm}_module' if e.kind == 'memory' else nm
        lines += [
            f'    // {nm}',
            f'    {mod_name} {nm}_inst (',
            f'        .{clk}({clk}),',
            f'        .{rst}({rst}),',
        ]
        for sig in ('awaddr', 'awvalid', 'awready', 'awprot',
                    'wdata', 'wstrb', 'wvalid', 'wready',
                    'bresp', 'bvalid', 'bready',
                    'araddr', 'arvalid', 'arready', 'arprot',
                    'rdata', 'rresp', 'rvalid', 'rready'):
            lines.append(f'        .{nm}_{sig}({nm}_{sig}),')
        lines[-1] = lines[-1].rstrip(',')
        lines += ['    );', '']

    lines.append('endmodule')
    return '\n'.join(lines)


# ── C header generation ────────────────────────────────────────────────────────

def gen_c_header(config: SocConfig, addr_map: AddressMap) -> str:
    """Generate C header with base addresses and size macros."""
    guard = f'_{config.name.upper()}_SOC_H'
    lines = [
        f'#ifndef {guard}',
        f'#define {guard}',
        '',
        '#include <stdint.h>',
        '',
        f'/* Auto-generated memory map for {config.name} */',
        '',
    ]

    for e in addr_map:
        nm = e.name.upper()
        lines += [
            f'#define {nm}_BASE  0x{e.base:08X}UL',
            f'#define {nm}_SIZE  0x{e.size:08X}UL',
            f'#define {nm}_END   0x{e.base + e.size:08X}UL',
            '',
        ]

    lines += [
        f'#endif /* {guard} */',
        '',
    ]
    return '\n'.join(lines)


# ── Linker script generation ───────────────────────────────────────────────────

def gen_linker_script(config: SocConfig, addr_map: AddressMap) -> str:
    """Generate a GNU ld linker script from the memory map."""
    lines = [
        f'/* Auto-generated linker script for {config.name} */',
        '',
        'MEMORY',
        '{',
    ]

    for e in addr_map:
        if e.kind != 'memory':
            continue
        attrs = 'rx' if e.subtype == 'rom' else 'rwx'
        lines.append(
            f'    {e.name.upper():<12} ({"r" if e.subtype == "rom" else "rw"}x) '
            f': ORIGIN = 0x{e.base:08X}, LENGTH = 0x{e.size:08X}'
        )

    lines += [
        '}',
        '',
        'SECTIONS',
        '{',
    ]

    # Find first ROM and first RAM
    rom = next((e for e in addr_map if e.kind == 'memory' and e.subtype == 'rom'), None)
    ram = next((e for e in addr_map if e.kind == 'memory' and e.subtype == 'ram'), None)

    if rom:
        lines += [
            f'    .text : {{',
            f'        *(.text*)',
            f'        *(.rodata*)',
            f'    }} > {rom.name.upper()}',
            '',
        ]
    if ram:
        lines += [
            f'    .data : {{',
            f'        *(.data*)',
            f'    }} > {ram.name.upper()}',
            '',
            f'    .bss : {{',
            f'        *(.bss*)',
            f'        *(COMMON)',
            f'    }} > {ram.name.upper()}',
            '',
        ]

    lines += [
        '}',
        '',
    ]
    return '\n'.join(lines)


# ── Main build entry point ─────────────────────────────────────────────────────

def build_soc(config_path: str, output_dir: str) -> None:
    """Parse config, generate all outputs into output_dir."""
    config = parse_soc_config(config_path)
    addr_map = AddressMap(config)

    os.makedirs(output_dir, exist_ok=True)

    interconnect_v = build_interconnect(config, addr_map)
    top_v = build_top(config, addr_map)
    c_hdr = gen_c_header(config, addr_map)
    ld_script = gen_linker_script(config, addr_map)

    outputs = [
        (f'{config.name}_interconnect.v', interconnect_v),
        (f'{config.name}_top.v', top_v),
        (f'{config.name}.h', c_hdr),
        (f'{config.name}.ld', ld_script),
    ]

    for fname, content in outputs:
        out_path = os.path.join(output_dir, fname)
        with open(out_path, 'w') as f:
            f.write(content)
        print(f'  {out_path}')
