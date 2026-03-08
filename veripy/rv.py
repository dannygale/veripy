"""RV32I instruction builder — named constructors for quick program assembly.

Usage::

    from veripy.rv import addi, add, sub, ebreak, x0, x1, x2, x3, asm

    program = [addi(x1, x0, 10), addi(x2, x0, 20), add(x3, x1, x2), ebreak()]
    raw = asm(program)  # bytes, ready for load_bytes()
"""

import struct

# ── register constants ────────────────────────────────────────────────────────

x0 = 0; x1 = 1; x2 = 2; x3 = 3; x4 = 4; x5 = 5; x6 = 6; x7 = 7
x8 = 8; x9 = 9; x10 = 10; x11 = 11; x12 = 12; x13 = 13; x14 = 14; x15 = 15
x16 = 16; x17 = 17; x18 = 18; x19 = 19; x20 = 20; x21 = 21; x22 = 22; x23 = 23
x24 = 24; x25 = 25; x26 = 26; x27 = 27; x28 = 28; x29 = 29; x30 = 30; x31 = 31

# ABI aliases
zero = 0; ra = 1; sp = 2; gp = 3; tp = 4
t0 = 5; t1 = 6; t2 = 7; s0 = 8; fp = 8; s1 = 9
a0 = 10; a1 = 11; a2 = 12; a3 = 13; a4 = 14; a5 = 15; a6 = 16; a7 = 17
s2 = 18; s3 = 19; s4 = 20; s5 = 21; s6 = 22; s7 = 23
s8 = 24; s9 = 25; s10 = 26; s11 = 27; t3 = 28; t4 = 29; t5 = 30; t6 = 31

# ── encoding helpers ──────────────────────────────────────────────────────────

def _mask(v, bits):
    return v & ((1 << bits) - 1)

def _r(f7, rs2, rs1, f3, rd, op):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op

def _i(imm, rs1, f3, rd, op):
    return (_mask(imm, 12) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op

def _s(imm, rs2, rs1, f3, op):
    imm = _mask(imm, 12)
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 0x1F) << 7) | op

def _b(imm, rs2, rs1, f3, op):
    imm = _mask(imm, 13)
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | \
           (rs2 << 20) | (rs1 << 15) | (f3 << 12) | \
           (((imm >> 1) & 0xF) << 8) | (((imm >> 11) & 1) << 7) | op

def _u(imm, rd, op):
    return (_mask(imm, 32) & 0xFFFFF000) | (rd << 7) | op

def _j(imm, rd, op):
    imm = _mask(imm, 21)
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | \
           (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) | (rd << 7) | op

# ── R-type (OP = 0x33) ───────────────────────────────────────────────────────

def add(rd, rs1, rs2):  return _r(0x00, rs2, rs1, 0, rd, 0x33)
def sub(rd, rs1, rs2):  return _r(0x20, rs2, rs1, 0, rd, 0x33)
def sll(rd, rs1, rs2):  return _r(0x00, rs2, rs1, 1, rd, 0x33)
def slt(rd, rs1, rs2):  return _r(0x00, rs2, rs1, 2, rd, 0x33)
def sltu(rd, rs1, rs2): return _r(0x00, rs2, rs1, 3, rd, 0x33)
def xor(rd, rs1, rs2):  return _r(0x00, rs2, rs1, 4, rd, 0x33)
def srl(rd, rs1, rs2):  return _r(0x00, rs2, rs1, 5, rd, 0x33)
def sra(rd, rs1, rs2):  return _r(0x20, rs2, rs1, 5, rd, 0x33)
def or_(rd, rs1, rs2):  return _r(0x00, rs2, rs1, 6, rd, 0x33)
def and_(rd, rs1, rs2): return _r(0x00, rs2, rs1, 7, rd, 0x33)

# ── I-type arithmetic (OP_IMM = 0x13) ────────────────────────────────────────

def addi(rd, rs1, imm):  return _i(imm, rs1, 0, rd, 0x13)
def slti(rd, rs1, imm):  return _i(imm, rs1, 2, rd, 0x13)
def sltiu(rd, rs1, imm): return _i(imm, rs1, 3, rd, 0x13)
def xori(rd, rs1, imm):  return _i(imm, rs1, 4, rd, 0x13)
def ori(rd, rs1, imm):   return _i(imm, rs1, 6, rd, 0x13)
def andi(rd, rs1, imm):  return _i(imm, rs1, 7, rd, 0x13)
def slli(rd, rs1, shamt): return _r(0x00, shamt, rs1, 1, rd, 0x13)
def srli(rd, rs1, shamt): return _r(0x00, shamt, rs1, 5, rd, 0x13)
def srai(rd, rs1, shamt): return _r(0x20, shamt, rs1, 5, rd, 0x13)

# ── I-type loads (LOAD = 0x03) ────────────────────────────────────────────────

def lb(rd, rs1, imm=0):  return _i(imm, rs1, 0, rd, 0x03)
def lh(rd, rs1, imm=0):  return _i(imm, rs1, 1, rd, 0x03)
def lw(rd, rs1, imm=0):  return _i(imm, rs1, 2, rd, 0x03)
def lbu(rd, rs1, imm=0): return _i(imm, rs1, 4, rd, 0x03)
def lhu(rd, rs1, imm=0): return _i(imm, rs1, 5, rd, 0x03)

# ── S-type stores (STORE = 0x23) ─────────────────────────────────────────────

def sb(rs2, rs1, imm=0): return _s(imm, rs2, rs1, 0, 0x23)
def sh(rs2, rs1, imm=0): return _s(imm, rs2, rs1, 1, 0x23)
def sw(rs2, rs1, imm=0): return _s(imm, rs2, rs1, 2, 0x23)

# ── B-type branches (BRANCH = 0x63) ──────────────────────────────────────────

def beq(rs1, rs2, imm):  return _b(imm, rs2, rs1, 0, 0x63)
def bne(rs1, rs2, imm):  return _b(imm, rs2, rs1, 1, 0x63)
def blt(rs1, rs2, imm):  return _b(imm, rs2, rs1, 4, 0x63)
def bge(rs1, rs2, imm):  return _b(imm, rs2, rs1, 5, 0x63)
def bltu(rs1, rs2, imm): return _b(imm, rs2, rs1, 6, 0x63)
def bgeu(rs1, rs2, imm): return _b(imm, rs2, rs1, 7, 0x63)

# ── U-type ────────────────────────────────────────────────────────────────────

def lui(rd, imm):   return _u(imm, rd, 0x37)
def auipc(rd, imm): return _u(imm, rd, 0x17)

# ── J-type ────────────────────────────────────────────────────────────────────

def jal(rd, imm):        return _j(imm, rd, 0x6F)
def jalr(rd, rs1, imm=0): return _i(imm, rs1, 0, rd, 0x67)

# ── system ────────────────────────────────────────────────────────────────────

def ecall():  return 0x00000073
def ebreak(): return 0x00100073

# ── pseudo-instructions ───────────────────────────────────────────────────────

def nop():            return addi(x0, x0, 0)
def li(rd, imm):
    """Load immediate (up to 32-bit). Returns list of 1-2 instructions."""
    imm = imm & 0xFFFFFFFF
    lo = imm & 0xFFF
    if lo >= 0x800:
        lo -= 0x1000
    hi = (imm - (lo & 0xFFFFFFFF)) & 0xFFFFFFFF
    if hi == 0:
        return [addi(rd, x0, imm if imm < 0x800 else imm - 0x1000)]
    if lo == 0:
        return [lui(rd, hi)]
    return [lui(rd, hi), addi(rd, rd, lo & 0xFFF)]

def mv(rd, rs1):      return addi(rd, rs1, 0)
def not_(rd, rs1):    return xori(rd, rs1, -1)
def neg(rd, rs1):     return sub(rd, x0, rs1)
def j(imm):           return jal(x0, imm)
def jr(rs1):          return jalr(x0, rs1)
def ret():            return jalr(x0, ra)
def call(rd, imm):    return jal(rd, imm)

# ── assembly helper ───────────────────────────────────────────────────────────

def asm(instrs):
    """Pack a list of instruction ints (or nested lists from pseudo-ops) to bytes."""
    flat = []
    for item in instrs:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    return struct.pack(f'<{len(flat)}I', *flat)


class QuickRunMixin:
    """Mixin for CPU modules that provides a one-call load-and-execute helper.

    The host class must provide: load_bytes(addr, data), run(max_cycles=),
    xreg(i), and a reset_pc attribute or constructor kwarg.

    Usage::

        class CPU(Module, QuickRunMixin):
            def __init__(self, reset_pc=0x8000_0000):
                self.reset_pc = reset_pc
                ...

        cpu = CPU()
        regs = cpu.quick_run([addi(x1, x0, 42), ebreak()])
        assert regs[1] == 42
    """

    def quick_run(self, instrs, max_cycles=2000, base=None):
        """Assemble, load, run, return register snapshot dict.

        Args:
            instrs: list of instruction ints (from rv.* constructors)
            max_cycles: simulation cycle limit
            base: load address (default: self.reset_pc)

        Returns:
            dict mapping register index (0-31) to int value
        """
        addr = base if base is not None else self.reset_pc
        self.load_bytes(addr, asm(instrs))
        halted = self.run(max_cycles=max_cycles)
        regs = {i: self.xreg(i) for i in range(32)}
        regs['halted'] = halted
        return regs
