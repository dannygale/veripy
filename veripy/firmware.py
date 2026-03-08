"""ELF loader for firmware co-simulation.

Parses ELF32/ELF64 little-endian binaries and extracts PT_LOAD segments
into an ElfImage suitable for loading into a simulated memory model.
"""

import struct
from dataclasses import dataclass, field

# ELF constants
_ELFMAG      = b'\x7fELF'
_ELFCLASS32  = 1
_ELFCLASS64  = 2
_ELFDATA2LSB = 1   # little-endian
_PT_LOAD     = 1

# Segment flags
PF_X = 0x1   # execute
PF_W = 0x2   # write
PF_R = 0x4   # read


@dataclass
class ElfSegment:
    vaddr:  int    # virtual load address
    data:   bytes  # file bytes (filesz)
    memsz:  int    # total memory size (>= len(data); excess is BSS)
    flags:  int    # PF_R | PF_W | PF_X


@dataclass
class ElfImage:
    entry:    int
    bits:     int                          # 32 or 64
    segments: list[ElfSegment] = field(default_factory=list)


def load_elf(path: str) -> ElfImage:
    """Parse an ELF file and return an ElfImage with all PT_LOAD segments."""
    with open(path, 'rb') as f:
        raw = f.read()

    if raw[:4] != _ELFMAG:
        raise ValueError(f"{path}: not an ELF file")
    if raw[5] != _ELFDATA2LSB:
        raise ValueError(f"{path}: only little-endian ELF supported")

    ei_class = raw[4]
    if ei_class == _ELFCLASS32:
        return _parse32(raw)
    if ei_class == _ELFCLASS64:
        return _parse64(raw)
    raise ValueError(f"{path}: unknown ELF class {ei_class}")


# ── ELF32 ──────────────────────────────────────────────────────────────────────

# Header fields from offset 16: type(H) machine(H) version(I) entry(I)
#   phoff(I) shoff(I) flags(I) ehsize(H) phentsize(H) phnum(H) ...
_HDR32 = struct.Struct('<HHIIIIIHHHHHH')

# Program header: type(I) offset(I) vaddr(I) paddr(I) filesz(I) memsz(I) flags(I) align(I)
_PHDR32 = struct.Struct('<IIIIIIII')


def _parse32(raw: bytes) -> ElfImage:
    hdr = _HDR32.unpack_from(raw, 16)
    e_entry, e_phoff, e_phentsize, e_phnum = hdr[3], hdr[4], hdr[8], hdr[9]
    segments = []
    for i in range(e_phnum):
        ph = _PHDR32.unpack_from(raw, e_phoff + i * e_phentsize)
        p_type, p_offset, p_vaddr, _, p_filesz, p_memsz, p_flags, _ = ph
        if p_type == _PT_LOAD and p_memsz > 0:
            segments.append(ElfSegment(
                vaddr=p_vaddr,
                data=raw[p_offset:p_offset + p_filesz],
                memsz=p_memsz,
                flags=p_flags,
            ))
    return ElfImage(entry=e_entry, bits=32, segments=segments)


# ── ELF64 ──────────────────────────────────────────────────────────────────────

# Header fields from offset 16: type(H) machine(H) version(I) entry(Q)
#   phoff(Q) shoff(Q) flags(I) ehsize(H) phentsize(H) phnum(H) ...
_HDR64 = struct.Struct('<HHIQQQIHHHHHH')

# Program header: type(I) flags(I) offset(Q) vaddr(Q) paddr(Q) filesz(Q) memsz(Q) align(Q)
_PHDR64 = struct.Struct('<IIQQQQQQ')


def _parse64(raw: bytes) -> ElfImage:
    hdr = _HDR64.unpack_from(raw, 16)
    e_entry, e_phoff, e_phentsize, e_phnum = hdr[3], hdr[4], hdr[8], hdr[9]
    segments = []
    for i in range(e_phnum):
        ph = _PHDR64.unpack_from(raw, e_phoff + i * e_phentsize)
        p_type, p_flags, p_offset, p_vaddr, _, p_filesz, p_memsz, _ = ph
        if p_type == _PT_LOAD and p_memsz > 0:
            segments.append(ElfSegment(
                vaddr=p_vaddr,
                data=raw[p_offset:p_offset + p_filesz],
                memsz=p_memsz,
                flags=p_flags,
            ))
    return ElfImage(entry=e_entry, bits=64, segments=segments)
