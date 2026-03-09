#!/usr/bin/env python3
"""cpu_v1: self-contained 3-stage pipeline CPU for csim benchmarking.

Pipeline stages
---------------
  IF/ID : fetch from imem, decode instruction, read register file
  EX    : ALU execution with WB→EX forwarding; branch/jump resolved here
  WB    : write-back to register file, data-memory access

ISA (16-bit instructions, 16-bit data, 8 registers r0..r7)
-----------------------------------------------------------
  Encoding  [15:11]=opcode  [10:8]=rd  [7:5]=rs1  [4:2]=rs2  [7:0]=imm8

  ADD  rd, rs1, rs2   — rd = rs1 + rs2
  SUB  rd, rs1, rs2   — rd = rs1 - rs2
  AND  rd, rs1, rs2   — rd = rs1 & rs2
  OR   rd, rs1, rs2   — rd = rs1 | rs2
  LI   rd, imm8       — rd = zero_extend(imm8)
  ADDI rd, imm8       — rd = rd + sign_extend(imm8)  (rs1 = rd field)
  LOAD rd, rs1, imm8  — rd = dmem[rs1 + imm8]
  STORE rs2, rs1, imm8 — dmem[rs1 + imm8] = rs2  (rd field = rs2 src)
  JMP  imm8           — pc += sign_extend(imm8)  (unconditional, resolved in EX)
  BEQ  rs1, rs2, imm5 — if rs1==rs2: pc += sign_extend(imm5)  (resolved in EX)
                         encoding: [10:8]=rs1, [7:5]=rs2, [4:0]=imm5
  HLT                 — halt

Hazard handling
---------------
  EX-stage stall : stall IF/ID when EX stage will write a register that
                   ID stage reads (covers load-use and branch-after-write).
  Branch/jump flush : flush IF/ID+ID/EX on the cycle after a taken
                      branch/jump is resolved in EX (2-cycle penalty).

Loop-free design
----------------
  stall and flush signals are computed purely from pipeline-register
  outputs (sequential signals).  They feed only into the sequential
  capture blocks of the pipeline registers — never back into any
  combinational block that stall/flush depends on.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from veripy import Module, Input, Output, Register, Mem

# ── Opcodes ──────────────────────────────────────────────────────────
OP_ADD   = 0x05
OP_SUB   = 0x06
OP_AND   = 0x07
OP_OR    = 0x08
OP_LI    = 0x04
OP_ADDI  = 0x13
OP_LOAD  = 0x01
OP_STORE = 0x02
OP_JMP   = 0x0D
OP_BEQ   = 0x0E
OP_HLT   = 0x0F

# ── ALU op codes ─────────────────────────────────────────────────────
ALU_ADD  = 0
ALU_SUB  = 1
ALU_AND  = 2
ALU_OR   = 3
ALU_PASS = 9   # b → result (used for LI)

MEM_DEPTH = 256   # instruction / data memory depth (words)
REG_DEPTH = 8     # number of registers
DATA_W    = 16    # data width
REG_AW    = 3     # register address width


class CPU(Module):
    """3-stage pipelined CPU.

    Ports
    -----
    clock, reset  — standard clock/reset
    halted        — asserted when HLT instruction reaches WB stage
    """

    def __init__(self):
        self.clock  = Input()
        self.reset  = Input()
        self.halted = Output()

        self.imem    = Mem(MEM_DEPTH, DATA_W)
        self.dmem    = Mem(MEM_DEPTH, DATA_W)
        self.regfile = Mem(REG_DEPTH, DATA_W)

        self.if_id_instr = Register(DATA_W)
        self.if_id_valid = Register()
        self.if_id_pc    = Register(DATA_W)

        self.ex_rs1_data = Register(DATA_W)
        self.ex_rs2_data = Register(DATA_W)
        self.ex_imm      = Register(DATA_W)
        self.ex_imm5     = Register(5)
        self.ex_rd_addr  = Register(REG_AW)
        self.ex_rs1_addr = Register(REG_AW)
        self.ex_rs2_addr = Register(REG_AW)
        self.ex_alu_op   = Register(4)
        self.ex_reg_we   = Register()
        self.ex_mem_re   = Register()
        self.ex_mem_we   = Register()
        self.ex_use_imm  = Register()
        self.ex_is_jump  = Register()
        self.ex_is_beq   = Register()
        self.ex_is_halt  = Register()
        self.ex_valid    = Register()
        self.ex_pc       = Register(DATA_W)

        self.wb_result   = Register(DATA_W)
        self.wb_rs2_data = Register(DATA_W)
        self.wb_mem_addr = Register(DATA_W)
        self.wb_rd_addr  = Register(REG_AW)
        self.wb_reg_we   = Register()
        self.wb_mem_re   = Register()
        self.wb_mem_we   = Register()
        self.wb_valid    = Register()
        self.wb_is_halt  = Register()

        self.pc = Register(DATA_W)

        from veripy import Signal
        self.id_rs1_addr   = Signal(REG_AW)
        self.id_rs2_addr   = Signal(REG_AW)
        self.id_rd_addr    = Signal(REG_AW)
        self.id_imm8       = Signal(DATA_W)
        self.id_imm5       = Signal(5)
        self.id_reg_we     = Signal()
        self.id_mem_re     = Signal()
        self.id_mem_we     = Signal()
        self.id_use_imm    = Signal()
        self.id_is_jump    = Signal()
        self.id_is_beq     = Signal()
        self.id_is_halt    = Signal()
        self.id_alu_op     = Signal(4)
        self.rf_rdata1     = Signal(DATA_W)
        self.rf_rdata2     = Signal(DATA_W)
        self.stall         = Signal()
        self.fwd_rs1       = Signal(DATA_W)
        self.fwd_rs2       = Signal(DATA_W)
        self.alu_result    = Signal(DATA_W)
        self.take_branch   = Signal()
        self.branch_target = Signal(DATA_W)
        self.wb_mem_rdata  = Signal(DATA_W)
        self.wb_wdata      = Signal(DATA_W)

        super().__init__()

    def rtl(self):
        # ── Decode ────────────────────────────────────────────────
        @self.comb
        def decode():
            instr  = self.if_id_instr
            opcode = instr[15:11]
            rd     = instr[10:8]
            rs1    = instr[7:5]
            rs2    = instr[4:2]
            imm8   = instr[7:0]
            imm5   = instr[4:0]

            # rs1 source: rd field for ADDI/BEQ
            if opcode == OP_ADDI or opcode == OP_BEQ:
                self.id_rs1_addr = rd
            else:
                self.id_rs1_addr = rs1

            # rs2 source: rd field for STORE; rs1 field for BEQ
            if opcode == OP_STORE:
                self.id_rs2_addr = rd
            elif opcode == OP_BEQ:
                self.id_rs2_addr = rs1
            else:
                self.id_rs2_addr = rs2

            self.id_rd_addr = rd
            self.id_imm5    = imm5

            # Sign-extend imm8 for ADDI; zero-extend for others
            if opcode == OP_ADDI and imm8[7]:
                self.id_imm8 = imm8 | 0xFF00
            else:
                self.id_imm8 = imm8

            # Control signals
            if (opcode == OP_ADD or opcode == OP_SUB or opcode == OP_AND or
                    opcode == OP_OR or opcode == OP_LI or opcode == OP_ADDI or
                    opcode == OP_LOAD):
                self.id_reg_we = 1
            else:
                self.id_reg_we = 0

            self.id_mem_re = 1 if opcode == OP_LOAD  else 0
            self.id_mem_we = 1 if opcode == OP_STORE else 0

            if (opcode == OP_LI or opcode == OP_ADDI or
                    opcode == OP_LOAD or opcode == OP_STORE):
                self.id_use_imm = 1
            else:
                self.id_use_imm = 0

            self.id_is_jump = 1 if opcode == OP_JMP else 0
            self.id_is_beq  = 1 if opcode == OP_BEQ else 0
            self.id_is_halt = 1 if opcode == OP_HLT else 0

            if opcode == OP_SUB:
                self.id_alu_op = ALU_SUB
            elif opcode == OP_AND:
                self.id_alu_op = ALU_AND
            elif opcode == OP_OR:
                self.id_alu_op = ALU_OR
            elif opcode == OP_LI:
                self.id_alu_op = ALU_PASS
            else:
                self.id_alu_op = ALU_ADD   # ADD, ADDI, LOAD, STORE, others

        # ── Register file reads with WB→ID forwarding ────────────
        @self.comb
        def rf_read():
            # Forward from WB stage if it's writing the same register
            if self.wb_reg_we and self.wb_rd_addr and self.wb_rd_addr == self.id_rs1_addr:
                self.rf_rdata1 = self.wb_wdata
            else:
                self.rf_rdata1 = self.regfile[self.id_rs1_addr]
            if self.wb_reg_we and self.wb_rd_addr and self.wb_rd_addr == self.id_rs2_addr:
                self.rf_rdata2 = self.wb_wdata
            else:
                self.rf_rdata2 = self.regfile[self.id_rs2_addr]

        # ── Hazard detection ─────────────────────────────────────
        # Stall when EX stage will write a register that ID stage reads.
        # Reads only from pipeline registers (sequential) — no loop.
        @self.comb
        def hazard():
            if (self.ex_valid and self.ex_reg_we and self.ex_rd_addr and
                    (self.ex_rd_addr == self.id_rs1_addr or
                     self.ex_rd_addr == self.id_rs2_addr)):
                self.stall = 1
            else:
                self.stall = 0

        # ── WB→EX forwarding mux ─────────────────────────────────
        # Reads from WB pipeline register (sequential) — no loop.
        @self.comb
        def forward():
            if (self.wb_valid and self.wb_reg_we and self.wb_rd_addr and
                    self.wb_rd_addr == self.ex_rs1_addr):
                self.fwd_rs1 = self.wb_result
            else:
                self.fwd_rs1 = self.ex_rs1_data

            if (self.wb_valid and self.wb_reg_we and self.wb_rd_addr and
                    self.wb_rd_addr == self.ex_rs2_addr):
                self.fwd_rs2 = self.wb_result
            else:
                self.fwd_rs2 = self.ex_rs2_data

        # ── ALU ───────────────────────────────────────────────────
        @self.comb
        def alu():
            a  = self.fwd_rs1
            b  = self.ex_imm if self.ex_use_imm else self.fwd_rs2
            if self.ex_alu_op == ALU_SUB:
                self.alu_result = a - b
            elif self.ex_alu_op == ALU_AND:
                self.alu_result = a & b
            elif self.ex_alu_op == ALU_OR:
                self.alu_result = a | b
            elif self.ex_alu_op == ALU_PASS:
                self.alu_result = b
            else:
                self.alu_result = a + b

        # ── Branch/jump resolution (EX stage) ────────────────────
        # Reads from EX pipeline registers (sequential) — no loop.
        @self.comb
        def branch_resolve():
            if self.ex_valid and self.ex_is_beq and self.fwd_rs1 == self.fwd_rs2:
                self.take_branch = 1
            elif self.ex_valid and self.ex_is_jump:
                self.take_branch = 1
            else:
                self.take_branch = 0

            # Branch target: ex_pc + sign_extend(imm8 or imm5)
            if self.ex_is_jump:
                if self.ex_imm[7]:
                    self.branch_target = self.ex_pc + (self.ex_imm | 0xFF00)
                else:
                    self.branch_target = self.ex_pc + self.ex_imm
            else:
                if self.ex_imm5[4]:
                    self.branch_target = self.ex_pc + (self.ex_imm5 | 0xFFE0)
                else:
                    self.branch_target = self.ex_pc + self.ex_imm5

        # ── Data memory read (WB stage) ───────────────────────────
        @self.comb
        def dmem_read():
            self.wb_mem_rdata = self.dmem[self.wb_mem_addr]

        # ── Write-back data mux ───────────────────────────────────
        @self.comb
        def wb_mux():
            if self.wb_mem_re:
                self.wb_wdata = self.wb_mem_rdata
            else:
                self.wb_wdata = self.wb_result

        # ── Sequential: PC + pipeline registers ───────────────────
        @self.posedge(self.clock)
        def seq():
            if self.reset:
                self.pc          = 0
                self.if_id_valid = 0
                self.ex_valid    = 0
                self.wb_valid    = 0
                self.halted      = 0
            else:
                # ── PC update ─────────────────────────────────────
                if self.take_branch:
                    self.pc = self.branch_target
                elif not self.stall:
                    self.pc = self.pc + 1

                # ── IF/ID register ────────────────────────────────
                if self.take_branch:
                    self.if_id_instr = 0
                    self.if_id_valid = 0
                    self.if_id_pc    = 0
                elif not self.stall:
                    self.if_id_instr = self.imem[self.pc]
                    self.if_id_valid = 1
                    self.if_id_pc    = self.pc

                # ── ID/EX register ────────────────────────────────
                if self.take_branch or self.stall or not self.if_id_valid:
                    self.ex_valid   = 0
                    self.ex_reg_we  = 0
                    self.ex_mem_re  = 0
                    self.ex_mem_we  = 0
                    self.ex_is_jump = 0
                    self.ex_is_beq  = 0
                    self.ex_is_halt = 0
                else:
                    self.ex_rs1_data = self.rf_rdata1
                    self.ex_rs2_data = self.rf_rdata2
                    self.ex_imm      = self.id_imm8
                    self.ex_imm5     = self.id_imm5
                    self.ex_rd_addr  = self.id_rd_addr
                    self.ex_rs1_addr = self.id_rs1_addr
                    self.ex_rs2_addr = self.id_rs2_addr
                    self.ex_alu_op   = self.id_alu_op
                    self.ex_reg_we   = self.id_reg_we
                    self.ex_mem_re   = self.id_mem_re
                    self.ex_mem_we   = self.id_mem_we
                    self.ex_use_imm  = self.id_use_imm
                    self.ex_is_jump  = self.id_is_jump
                    self.ex_is_beq   = self.id_is_beq
                    self.ex_is_halt  = self.id_is_halt
                    self.ex_valid    = 1
                    self.ex_pc       = self.if_id_pc

                # ── EX/WB register ────────────────────────────────
                self.wb_result   = self.alu_result
                self.wb_rs2_data = self.fwd_rs2
                self.wb_mem_addr = self.alu_result
                self.wb_rd_addr  = self.ex_rd_addr
                self.wb_reg_we   = self.ex_reg_we and self.ex_valid
                self.wb_mem_re   = self.ex_mem_re and self.ex_valid
                self.wb_mem_we   = self.ex_mem_we and self.ex_valid
                self.wb_valid    = self.ex_valid
                self.wb_is_halt  = self.ex_is_halt and self.ex_valid

                # ── Write-back to register file ───────────────────
                if self.wb_reg_we and self.wb_rd_addr:
                    self.regfile.write(self.wb_rd_addr, self.wb_wdata)

                # ── Data memory write ─────────────────────────────
                if self.wb_mem_we:
                    self.dmem.write(self.wb_mem_addr, self.wb_rs2_data)

                # ── Halt ──────────────────────────────────────────
                if self.wb_is_halt:
                    self.halted = 1


# ── Instruction encoding helpers ─────────────────────────────────────

def _enc(opcode, rd=0, rs1=0, rs2=0, imm=0):
    return ((opcode & 0x1F) << 11 | (rd & 7) << 8 |
            (rs1 & 7) << 5 | (rs2 & 7) << 2 | (imm & 0xFF))

def ADD(rd, rs1, rs2):    return _enc(OP_ADD,   rd, rs1, rs2)
def SUB(rd, rs1, rs2):    return _enc(OP_SUB,   rd, rs1, rs2)
def AND(rd, rs1, rs2):    return _enc(OP_AND,   rd, rs1, rs2)
def OR(rd, rs1, rs2):     return _enc(OP_OR,    rd, rs1, rs2)
def LI(rd, imm):          return _enc(OP_LI,    rd, imm=imm & 0xFF)
def ADDI(rd, imm):        return _enc(OP_ADDI,  rd, imm=imm & 0xFF)
def LOAD(rd, rs1, imm):   return _enc(OP_LOAD,  rd, rs1, imm=imm & 0xFF)
def STORE(rs2, rs1, imm): return _enc(OP_STORE, rs2, rs1, imm=imm & 0xFF)
def JMP(imm):             return _enc(OP_JMP,   imm=imm & 0xFF)
def BEQ(rs1, rs2, imm5):
    # [10:8]=rs1, [7:5]=rs2, [4:0]=imm5
    return ((OP_BEQ & 0x1F) << 11 | (rs1 & 7) << 8 |
            (rs2 & 7) << 5 | (imm5 & 0x1F))
def HLT():                return _enc(OP_HLT)
def NOP():                return _enc(OP_ADD)


# ── Benchmark programs ────────────────────────────────────────────────

def prog_fibonacci(n=200):
    """Compute fib(n) in r1.  r1=a, r2=b, r3=counter, r4=tmp.
    Branch resolved in EX stage (2-cycle penalty per branch).
    """
    # PC layout:
    #  0: LI r1, 0
    #  1: LI r2, 1
    #  2: LI r3, n
    #  3: BEQ r3, r0, +6  → if r3==0 goto PC=9 (HLT)
    #  4: ADD r4, r1, r2
    #  5: ADD r1, r2, r0
    #  6: ADD r2, r4, r0
    #  7: ADDI r3, -1      (0xFF = -1 sign-extended)
    #  8: JMP -5           → goto PC=3 (loop)
    #  9: HLT
    return [
        LI(1, 0),           # 0
        LI(2, 1),           # 1
        LI(3, n & 0xFF),    # 2
        BEQ(3, 0, 6),       # 3: if r3==0 goto +6 = PC=9
        ADD(4, 1, 2),       # 4
        ADD(1, 2, 0),       # 5
        ADD(2, 4, 0),       # 6
        ADDI(3, 0xFF),      # 7: r3-- (0xFF = -1 sign-extended)
        JMP(0xFB),          # 8: jmp -5 → PC=3
        HLT(),              # 9
    ]


def prog_sum_array(count=200):
    """Sum 1+2+...+count into r1.  r2=counter."""
    # PC layout:
    #  0: LI r1, 0
    #  1: LI r2, count
    #  2: BEQ r2, r0, +4  → if r2==0 goto PC=6 (HLT)
    #  3: ADD r1, r1, r2
    #  4: ADDI r2, -1
    #  5: JMP -3           → goto PC=2
    #  6: HLT
    return [
        LI(1, 0),               # 0
        LI(2, count & 0xFF),    # 1
        BEQ(2, 0, 4),           # 2: if r2==0 goto +4 = PC=6
        ADD(1, 1, 2),           # 3
        ADDI(2, 0xFF),          # 4: r2--
        JMP(0xFD),              # 5: jmp -3 → PC=2
        HLT(),                  # 6
    ]


def prog_bubble_sort(n=32):
    """Bubble-sort n values in dmem[0..n-1] (initialised descending).
    Uses r1=outer, r2=inner, r3=n, r4=a, r5=b, r6=n-1.
    """
    # Initialise dmem with descending values
    init = []
    for i in range(n):
        init += [LI(4, (n - i) & 0xFF), STORE(4, 0, i)]

    base = len(init)
    # Outer loop: r1 = 0..n-1
    # Inner loop: r2 = 0..n-2
    sort = [
        LI(1, 0),               # base+0: outer = 0
        LI(3, n & 0xFF),        # base+1: r3 = n
        LI(6, (n-1) & 0xFF),    # base+2: r6 = n-1
        # outer loop (base+3):
        BEQ(1, 3, 13),          # base+3: if outer==n goto done (base+16)
        LI(2, 0),               # base+4: inner = 0
        # inner loop (base+5):
        BEQ(2, 6, 9),           # base+5: if inner==n-1 goto outer_next (base+14)
        LOAD(4, 2, 0),          # base+6: r4 = dmem[inner]
        LOAD(5, 2, 1),          # base+7: r5 = dmem[inner+1]
        SUB(7, 5, 4),           # base+8: r7 = b - a
        BEQ(7, 0, 2),           # base+9: if b==a skip swap (base+11)
        STORE(5, 2, 0),         # base+10: dmem[inner] = b
        STORE(4, 2, 1),         # base+11: dmem[inner+1] = a
        ADDI(2, 1),             # base+12: inner++
        JMP(0xF8),              # base+13: jmp -8 → base+5
        ADDI(1, 1),             # base+14: outer++
        JMP(0xF4),              # base+15: jmp -12 → base+3
        HLT(),                  # base+16
    ]
    return init + sort


# ── Simulation helper ─────────────────────────────────────────────────

def run_sim(prog, max_cycles=100000):
    """Run prog on the CPU in Python simulation, return cycle count."""
    from veripy.sim import SimEngine

    cpu = CPU()
    for i, w in enumerate(prog):
        cpu.imem._data[i] = w

    sim = SimEngine(cpu)
    sim.clock(cpu.clock, 10)
    cycles = [0]

    @sim.initial
    def run():
        cpu.reset.set(1); yield 20; cpu.reset.set(0)
        for c in range(max_cycles):
            yield 10
            cycles[0] = c + 1
            if int(cpu.halted):
                break

    sim.run()
    return cycles[0]


if __name__ == '__main__':
    import time

    print('=== cpu_v1 self-test ===')

    # Verify fibonacci(10) = 55
    cpu = CPU()
    prog = prog_fibonacci(10)
    for i, w in enumerate(prog):
        cpu.imem._data[i] = w

    from veripy.sim import SimEngine
    sim = SimEngine(cpu)
    sim.clock(cpu.clock, 10)

    @sim.initial
    def run():
        cpu.reset.set(1); yield 20; cpu.reset.set(0)
        for _ in range(500):
            yield 10
            if int(cpu.halted):
                break
        r1 = cpu.regfile._data[1]
        print(f'fib(10) = {r1}  (expected 55, {"PASS" if r1 == 55 else "FAIL"})')

    sim.run()

    # Benchmark
    for name, prog in [('fib(200)', prog_fibonacci(200)),
                       ('sum(200)', prog_sum_array(200))]:
        t0 = time.perf_counter()
        cyc = run_sim(prog)
        dt = time.perf_counter() - t0
        print(f'{name}: {cyc} cycles in {dt*1000:.1f}ms')

    # Verify flatten + topo_sort (no combinational loop)
    print()
    print('=== flatten + topo_sort ===')
    from veripy.lower import lower_module
    from veripy.flatten import flatten_ir, topo_sort_comb
    cpu2 = CPU()
    ir = lower_module(cpu2, 'cpu')
    flat = flatten_ir(ir, {})
    sorted_flat = topo_sort_comb(flat)
    print(f'OK: {len(sorted_flat.comb_blocks)} comb, {len(sorted_flat.seq_blocks)} seq, '
          f'{len(sorted_flat.mems)} mems')
