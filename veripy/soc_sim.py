"""SoC firmware co-simulation.

FlatMemory models a byte-addressable address space with named regions.
SocSim assembles memory regions and peripherals from a SocConfig, loads
firmware via load_elf(), and provides read/write dispatch for CPU simulation.
UartPeripheral captures character output from a UART TX register.
"""

from __future__ import annotations

from .firmware import load_elf
from .soc import SocConfig


# ── FlatMemory ─────────────────────────────────────────────────────────────────

class FlatMemory:
    """Byte-addressable memory model with multiple named regions."""

    def __init__(self) -> None:
        # list of (base, end_exclusive, bytearray, name)
        self._regions: list[tuple[int, int, bytearray, str]] = []

    def add_region(self, name: str, base: int, size: int) -> None:
        self._regions.append((base, base + size, bytearray(size), name))

    # ── internal ──────────────────────────────────────────────────────────────

    def _lookup(self, addr: int) -> tuple[bytearray, int] | None:
        for base, end, buf, _ in self._regions:
            if base <= addr < end:
                return buf, addr - base
        return None

    # ── public API ────────────────────────────────────────────────────────────

    def read(self, addr: int, size: int = 4) -> int:
        r = self._lookup(addr)
        if r is None:
            return 0
        buf, off = r
        val = 0
        for i, b in enumerate(buf[off:off + size]):
            val |= b << (8 * i)
        return val

    def write(self, addr: int, value: int, size: int = 4) -> None:
        r = self._lookup(addr)
        if r is None:
            return
        buf, off = r
        for i in range(size):
            buf[off + i] = (value >> (8 * i)) & 0xFF

    def load_bytes(self, addr: int, data: bytes | bytearray) -> None:
        """Write raw bytes starting at addr (may span at most one region)."""
        for i, byte in enumerate(data):
            r = self._lookup(addr + i)
            if r is not None:
                buf, off = r
                buf[off] = byte


# ── UartPeripheral ─────────────────────────────────────────────────────────────

class UartPeripheral:
    """Minimal UART model: writes to offset 0 (TX) are captured as text."""

    def __init__(self, base: int) -> None:
        self.base = base
        self.output: str = ''

    def read(self, addr: int, size: int) -> int:  # noqa: ARG002
        return 0

    def write(self, addr: int, value: int, size: int) -> None:  # noqa: ARG002
        if addr == self.base:
            self.output += chr(value & 0xFF)


# ── SocSim ─────────────────────────────────────────────────────────────────────

class SocSim:
    """SoC simulator: memory model + peripheral dispatch built from SocConfig.

    Usage::

        sim = SocSim(config)
        entry = sim.load_elf('firmware.elf')
        # drive a CPU model, calling sim.read() / sim.write() for bus access
        print(sim.uart.output)
    """

    def __init__(self, config: SocConfig) -> None:
        self.config = config
        self.memory = FlatMemory()
        self.uart: UartPeripheral | None = None
        # list of (base, end_exclusive, handler)
        self._periphs: list[tuple[int, int, UartPeripheral]] = []

        for region in config.memory:
            self.memory.add_region(region.name, region.base, region.size)

        for periph in config.peripherals:
            if periph.type == 'uart':
                u = UartPeripheral(periph.base)
                if self.uart is None:
                    self.uart = u
                self._periphs.append((periph.base, periph.base + periph.size, u))
            # other types: silently ignored (no-op peripheral)

    # ── firmware loading ──────────────────────────────────────────────────────

    def load_elf(self, path: str) -> int:
        """Load ELF firmware into memory regions. Returns entry point address."""
        img = load_elf(path)
        for seg in img.segments:
            self.memory.load_bytes(seg.vaddr, seg.data)
            # zero BSS (memsz > filesz)
            bss_len = seg.memsz - len(seg.data)
            if bss_len > 0:
                self.memory.load_bytes(seg.vaddr + len(seg.data), bytes(bss_len))
        return img.entry

    # ── bus access ────────────────────────────────────────────────────────────

    def read(self, addr: int, size: int = 4) -> int:
        """Read from the SoC address space (peripherals take priority)."""
        for base, end, handler in self._periphs:
            if base <= addr < end:
                return handler.read(addr, size)
        return self.memory.read(addr, size)

    def write(self, addr: int, value: int, size: int = 4) -> None:
        """Write to the SoC address space (peripherals take priority)."""
        for base, end, handler in self._periphs:
            if base <= addr < end:
                handler.write(addr, value, size)
                return
        self.memory.write(addr, value, size)
