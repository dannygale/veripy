"""Hardware IR — decouples Python AST from Verilog generation."""

from dataclasses import dataclass, field
from typing import Union


# ── Expressions ──────────────────────────────────────────────────────

class Expr:
    """Base class for IR expressions."""

@dataclass
class Const(Expr):
    value: int

@dataclass
class Param(Expr):
    name: str

@dataclass
class Sig(Expr):
    name: str

@dataclass
class BinOp(Expr):
    op: str          # '+', '-', '*', '/', '%', '&', '|', '^', '<<', '>>', '>>>'
    left: Expr
    right: Expr

@dataclass
class UnaryOp(Expr):
    op: str          # '~', '!'
    operand: Expr

@dataclass
class Compare(Expr):
    op: str          # '==', '!=', '<', '>', '<=', '>='
    left: Expr
    right: Expr

@dataclass
class BoolOp(Expr):
    op: str          # '&&', '||'
    values: list

@dataclass
class Mux(Expr):
    sel: Expr
    true_val: Expr
    false_val: Expr

@dataclass
class Slice(Expr):
    signal: Expr
    hi: Expr
    lo: Expr

@dataclass
class Index(Expr):
    signal: Expr
    idx: Expr

@dataclass
class Concat(Expr):
    parts: list      # list[Expr], MSB first


# ── Statements ───────────────────────────────────────────────────────

class Stmt:
    """Base class for IR statements."""

@dataclass
class Assign(Stmt):
    target: str
    value: Expr
    blocking: bool = True

@dataclass
class SliceAssign(Stmt):
    target: str
    hi: Expr
    lo: Expr
    value: Expr
    blocking: bool = True

@dataclass
class If(Stmt):
    cond: Expr
    then_body: list  # list[Stmt]
    else_body: list  # list[Stmt]

@dataclass
class Case(Stmt):
    sel: Expr
    cases: list      # list[(Expr, list[Stmt])]
    default: list    # list[Stmt] | None

@dataclass
class MemWrite(Stmt):
    mem: str
    addr: Expr
    data: Expr
    blocking: bool = True

@dataclass
class Delay(Stmt):
    value: Expr

@dataclass
class Display(Stmt):
    fmt: str
    args: list       # list[Expr]

@dataclass
class Finish(Stmt):
    pass

@dataclass
class Repeat(Stmt):
    count: Expr
    body: list       # list[Stmt]
    label: str = ''  # named block for disable (break)

@dataclass
class ForLoop(Stmt):
    var: str
    start: Expr
    stop: Expr
    body: list       # list[Stmt]
    label: str = ''  # named block for disable (break)

@dataclass
class Disable(Stmt):
    label: str


# ── Blocks ───────────────────────────────────────────────────────────

@dataclass
class ContAssign:
    target: str
    value: Expr

@dataclass
class CombBlock:
    stmts: list      # list[Stmt]
    locals: dict = field(default_factory=dict)  # name → width (for reg declarations)

@dataclass
class SeqBlock:
    edges: list      # list[(str, str)]  — [('posedge', 'clock'), ...]
    stmts: list      # list[Stmt]
    locals: dict = field(default_factory=dict)  # name → width (for reg declarations)

@dataclass
class InitialBlock:
    stmts: list      # list[Stmt]

@dataclass
class AlwaysBlock:
    stmts: list      # list[Stmt]


# ── Formal properties ────────────────────────────────────────────────

@dataclass
class FormalProperty:
    kind: str        # 'assert' | 'cover' | 'assume'
    clock: str       # clock signal name
    edge: str        # 'posedge' | 'negedge'
    expr: Expr       # boolean expression
    name: str        # property name (from function name)


# ── SVA temporal sequence IR nodes ───────────────────────────────────

class SeqExpr:
    """Base class for SVA sequence expressions."""

@dataclass
class SeqBool(SeqExpr):
    """A boolean expression as a one-cycle sequence."""
    expr: Expr

@dataclass
class SeqConcat(SeqExpr):
    """Sequence concatenation with delay: left ##[lo:hi] right."""
    left: SeqExpr
    right: SeqExpr
    lo: int = 1
    hi: int = 1      # -1 means $ (unbounded)

@dataclass
class SeqRepeat(SeqExpr):
    """Consecutive repetition: seq[*lo:hi]."""
    seq: SeqExpr
    lo: int = 1
    hi: int = 1      # -1 means $ (unbounded)

@dataclass
class SeqAnd(SeqExpr):
    """Sequence and: both must complete."""
    left: SeqExpr
    right: SeqExpr

@dataclass
class SeqOr(SeqExpr):
    """Sequence or: either must complete."""
    left: SeqExpr
    right: SeqExpr

@dataclass
class SeqNot(SeqExpr):
    """Sequence negation."""
    seq: SeqExpr

@dataclass
class SeqImplication(SeqExpr):
    """Implication: antecedent |-> consequent (overlapping) or |=> (non-overlapping)."""
    antecedent: SeqExpr
    consequent: SeqExpr
    overlapping: bool = True  # True = |->, False = |=>

@dataclass
class SeqWithin(SeqExpr):
    """Within: inner within outer."""
    inner: SeqExpr
    outer: SeqExpr

@dataclass
class SeqEventually(SeqExpr):
    """s_eventually: eventually the sequence holds."""
    seq: SeqExpr


@dataclass
class TemporalProperty:
    """SVA temporal property (sequence-based assertion)."""
    kind: str        # 'assert' | 'cover' | 'assume'
    clock: str       # clock signal name
    edge: str        # 'posedge' | 'negedge'
    seq: SeqExpr     # temporal sequence
    name: str        # property name


# ── Declarations ─────────────────────────────────────────────────────

@dataclass
class Port:
    name: str
    direction: str   # 'input' | 'output'
    width: Union[int, str]  # int or param expression string
    is_reg: bool = False

@dataclass
class WireDecl:
    name: str
    width: Union[int, str]

@dataclass
class RegDecl:
    name: str
    width: Union[int, str]

@dataclass
class MemDecl:
    name: str
    depth: int
    width: Union[int, str]
    style: str = None

@dataclass
class DualPortMemDecl:
    name: str
    depth: int
    width: Union[int, str]
    style: str = None
    clock: str = ''
    we: str = ''
    waddr: str = ''
    wdata: str = ''
    raddr: str = ''
    rdata: str = ''

@dataclass
class TrueDualPortMemDecl:
    name: str
    depth: int
    width: Union[int, str]
    style: str = None
    clka: str = ''
    wea: str = ''
    addra: str = ''
    dina: str = ''
    douta: str = ''
    clkb: str = ''
    web: str = ''
    addrb: str = ''
    dinb: str = ''
    doutb: str = ''

@dataclass
class Instance:
    mod_type: str
    inst_name: str
    params: dict     # param_name → value (int or str for param refs)
    ports: list      # list[(port_name, wire_name)]


# ── Module ───────────────────────────────────────────────────────────

@dataclass
class IRModule:
    name: str
    params: dict = field(default_factory=dict)       # name → default value
    ports: list = field(default_factory=list)         # list[Port]
    wires: list = field(default_factory=list)         # list[WireDecl]
    regs: list = field(default_factory=list)          # list[RegDecl]
    mems: list = field(default_factory=list)          # list[MemDecl]
    instances: list = field(default_factory=list)     # list[Instance]
    assigns: list = field(default_factory=list)       # list[ContAssign]
    comb_blocks: list = field(default_factory=list)   # list[CombBlock]
    seq_blocks: list = field(default_factory=list)    # list[SeqBlock]
    initial_blocks: list = field(default_factory=list) # list[InitialBlock]
    always_blocks: list = field(default_factory=list)  # list[AlwaysBlock]
    formal_props: list = field(default_factory=list)    # list[FormalProperty]
    temporal_props: list = field(default_factory=list)  # list[TemporalProperty]
