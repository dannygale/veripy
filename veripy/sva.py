"""SVA temporal sequence builder API and string parser."""

import re
from .ir import (
    Expr, Sig, Const, BinOp, Compare, BoolOp, UnaryOp,
    SeqExpr, SeqBool, SeqConcat, SeqRepeat,
    SeqAnd, SeqOr, SeqNot, SeqImplication, SeqWithin, SeqEventually,
)


# ── Builder API ───────────────────────────────────────────────────────

class _PendingDelay:
    """Intermediate object from seq.delay(N) — awaits .then(other)."""
    def __init__(self, left: 'Seq', lo: int, hi: int):
        self._left = left
        self._lo = lo
        self._hi = hi

    def then(self, other) -> 'Seq':
        right_ir = other._ir if isinstance(other, Seq) else SeqBool(_coerce_expr(other))
        return Seq(SeqConcat(self._left._ir, right_ir, self._lo, self._hi))


class Seq:
    """Composable SVA sequence builder.

    Usage::

        seq(valid).then(data_ok)          # valid ##1 data_ok
        seq(req).delay(2).then(ack)       # req ##2 ack
        seq(a).repeat(3)                  # a[*3]
        seq(a).implies(seq(b).then(c))    # a |-> b ##1 c
    """

    def __init__(self, ir: SeqExpr):
        self._ir = ir

    # ── concatenation ────────────────────────────────────────────────

    def then(self, other, lo: int = 1, hi: int = None) -> 'Seq':
        """Concatenate with delay: self ##[lo:hi] other."""
        if hi is None:
            hi = lo
        right_ir = other._ir if isinstance(other, Seq) else SeqBool(_coerce_expr(other))
        return Seq(SeqConcat(self._ir, right_ir, lo, hi))

    def delay(self, lo: int, hi: int = None) -> _PendingDelay:
        """Return a pending delay; call .then(other) to complete: self ##[lo:hi] other."""
        if hi is None:
            hi = lo
        return _PendingDelay(self, lo, hi)

    # ── repetition ───────────────────────────────────────────────────

    def repeat(self, lo: int, hi: int = None) -> 'Seq':
        """Consecutive repetition: self[*lo:hi]. hi=-1 means $."""
        if hi is None:
            hi = lo
        return Seq(SeqRepeat(self._ir, lo, hi))

    def repeat_star(self) -> 'Seq':
        """self[*] — zero or more repetitions."""
        return Seq(SeqRepeat(self._ir, 0, -1))

    def repeat_plus(self) -> 'Seq':
        """self[+] — one or more repetitions."""
        return Seq(SeqRepeat(self._ir, 1, -1))

    # ── implication ──────────────────────────────────────────────────

    def implies(self, other, overlapping: bool = True) -> 'Seq':
        """Implication: self |-> other (overlapping) or |=> (non-overlapping)."""
        right_ir = other._ir if isinstance(other, Seq) else SeqBool(_coerce_expr(other))
        return Seq(SeqImplication(self._ir, right_ir, overlapping))

    # ── other operators ──────────────────────────────────────────────

    def within(self, other) -> 'Seq':
        """self within other."""
        right_ir = other._ir if isinstance(other, Seq) else SeqBool(_coerce_expr(other))
        return Seq(SeqWithin(self._ir, right_ir))

    def eventually(self) -> 'Seq':
        """s_eventually self."""
        return Seq(SeqEventually(self._ir))

    def __and__(self, other) -> 'Seq':
        right_ir = other._ir if isinstance(other, Seq) else SeqBool(_coerce_expr(other))
        return Seq(SeqAnd(self._ir, right_ir))

    def __or__(self, other) -> 'Seq':
        right_ir = other._ir if isinstance(other, Seq) else SeqBool(_coerce_expr(other))
        return Seq(SeqOr(self._ir, right_ir))

    def __invert__(self) -> 'Seq':
        return Seq(SeqNot(self._ir))

    @property
    def ir(self) -> SeqExpr:
        return self._ir


def seq(expr) -> Seq:
    """Create a sequence from a boolean expression or signal.

    ``expr`` may be a VeriPy signal/expression or a raw :class:`~veripy.ir.Expr` IR node.
    """
    if isinstance(expr, SeqExpr):
        return Seq(expr)
    return Seq(SeqBool(_coerce_expr(expr)))


def _coerce_expr(val) -> Expr:
    """Convert a Python value or VeriPy signal to an IR Expr."""
    if isinstance(val, Expr):
        return val
    # VeriPy signals expose _ir or _expr; fall back to Sig by name
    if hasattr(val, '_ir'):
        return val._ir
    if hasattr(val, 'name'):
        return Sig(val.name)
    if isinstance(val, int):
        return Const(val)
    raise TypeError(f"Cannot convert {type(val).__name__!r} to IR Expr")


# ── SVA string parser ─────────────────────────────────────────────────

# Token types
_TK_IDENT   = 'IDENT'
_TK_INT     = 'INT'
_TK_DELAY   = 'DELAY'    # ##
_TK_IMPL_OV = 'IMPL_OV'  # |->
_TK_IMPL_NO = 'IMPL_NO'  # |=>
_TK_LBRACK  = 'LBRACK'   # [
_TK_RBRACK  = 'RBRACK'   # ]
_TK_LPAREN  = 'LPAREN'   # (
_TK_RPAREN  = 'RPAREN'   # )
_TK_STAR    = 'STAR'     # *
_TK_PLUS    = 'PLUS'     # +
_TK_COLON   = 'COLON'    # :
_TK_DOLLAR  = 'DOLLAR'   # $
_TK_AND     = 'AND'      # &&
_TK_OR      = 'OR'       # ||
_TK_NOT     = 'NOT'      # !
_TK_EQ      = 'EQ'       # ==
_TK_NEQ     = 'NEQ'      # !=
_TK_LT      = 'LT'       # <
_TK_GT      = 'GT'       # >
_TK_LE      = 'LE'       # <=
_TK_GE      = 'GE'       # >=
_TK_EOF     = 'EOF'

_TOKEN_RE = re.compile(
    r'\s*(?:'
    r'(##)'
    r'|(\|->)'
    r'|(\|=>)'
    r'|(&&)'
    r'|(\|\|)'
    r'|(!=)'
    r'|(==)'
    r'|(<=)'
    r'|(>=)'
    r'|(<)'
    r'|(>)'
    r'|(\[)'
    r'|(\])'
    r'|(\()'
    r'|(\))'
    r'|(\*)'
    r'|(\+)'
    r'|(:)'
    r'|(\$)'
    r'|(!)'
    r'|([A-Za-z_][A-Za-z0-9_]*)'
    r'|([0-9]+)'
    r')\s*',
    re.ASCII,
)

_TOKEN_TYPES = [
    _TK_DELAY, _TK_IMPL_OV, _TK_IMPL_NO,
    _TK_AND, _TK_OR,
    _TK_NEQ, _TK_EQ, _TK_LE, _TK_GE, _TK_LT, _TK_GT,
    _TK_LBRACK, _TK_RBRACK, _TK_LPAREN, _TK_RPAREN,
    _TK_STAR, _TK_PLUS, _TK_COLON, _TK_DOLLAR, _TK_NOT,
    _TK_IDENT, _TK_INT,
]


def _tokenize(s: str) -> list:
    tokens = []
    pos = 0
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m:
            raise SyntaxError(f"Unexpected character at position {pos}: {s[pos:]!r}")
        pos = m.end()
        for i, tt in enumerate(_TOKEN_TYPES):
            if m.group(i + 1) is not None:
                val = m.group(i + 1)
                tokens.append((tt, val))
                break
    tokens.append((_TK_EOF, ''))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    def _peek(self):
        return self._tokens[self._pos]

    def _consume(self, expected=None):
        tok = self._tokens[self._pos]
        if expected and tok[0] != expected:
            raise SyntaxError(f"Expected {expected}, got {tok}")
        self._pos += 1
        return tok

    def _at(self, *types):
        return self._peek()[0] in types

    # ── grammar ──────────────────────────────────────────────────────

    def parse_property(self) -> SeqExpr:
        """property ::= sequence ('|->' | '|=>') sequence | sequence"""
        left = self._parse_sequence()
        if self._at(_TK_IMPL_OV, _TK_IMPL_NO):
            overlapping = self._consume()[0] == _TK_IMPL_OV
            right = self._parse_sequence()
            return SeqImplication(left, right, overlapping)
        return left

    def _parse_sequence(self) -> SeqExpr:
        """sequence ::= repeat_seq ('##' delay_spec repeat_seq)*"""
        node = self._parse_repeat()
        while self._at(_TK_DELAY):
            self._consume(_TK_DELAY)
            lo, hi = self._parse_delay_spec()
            if hi is None:
                hi = lo
            right = self._parse_repeat()
            node = SeqConcat(node, right, lo, hi)
        return node

    def _parse_delay_spec(self):
        """delay_spec ::= INT | '[' INT ':' (INT | '$') ']'"""
        if self._at(_TK_LBRACK):
            self._consume(_TK_LBRACK)
            lo = int(self._consume(_TK_INT)[1])
            self._consume(_TK_COLON)
            if self._at(_TK_DOLLAR):
                self._consume(_TK_DOLLAR)
                hi = -1
            else:
                hi = int(self._consume(_TK_INT)[1])
            self._consume(_TK_RBRACK)
            return lo, hi
        return int(self._consume(_TK_INT)[1]), None  # hi=None → same as lo

    def _parse_repeat(self) -> SeqExpr:
        """repeat_seq ::= atom ('[*' range ']')?"""
        node = self._parse_atom()
        if self._at(_TK_LBRACK):
            # peek ahead for [* or [+ or [=
            saved = self._pos
            self._consume(_TK_LBRACK)
            if self._at(_TK_STAR):
                self._consume(_TK_STAR)
                if self._at(_TK_RBRACK):
                    self._consume(_TK_RBRACK)
                    node = SeqRepeat(node, 0, -1)
                else:
                    lo, hi = self._parse_range()
                    self._consume(_TK_RBRACK)
                    node = SeqRepeat(node, lo, hi)
            elif self._at(_TK_PLUS):
                self._consume(_TK_PLUS)
                self._consume(_TK_RBRACK)
                node = SeqRepeat(node, 1, -1)
            else:
                # Not a repetition — backtrack
                self._pos = saved
        return node

    def _parse_range(self):
        """range ::= INT (':' (INT | '$'))?"""
        lo = int(self._consume(_TK_INT)[1])
        if self._at(_TK_COLON):
            self._consume(_TK_COLON)
            if self._at(_TK_DOLLAR):
                self._consume(_TK_DOLLAR)
                return lo, -1
            hi = int(self._consume(_TK_INT)[1])
            return lo, hi
        return lo, lo

    def _parse_atom(self) -> SeqExpr:
        """atom ::= '(' property ')' | bool_expr"""
        if self._at(_TK_LPAREN):
            self._consume(_TK_LPAREN)
            node = self.parse_property()
            self._consume(_TK_RPAREN)
            return node
        return SeqBool(self._parse_bool_or())

    # ── boolean expression sub-parser ────────────────────────────────

    def _parse_bool_or(self) -> Expr:
        left = self._parse_bool_and()
        while self._at(_TK_OR):
            self._consume(_TK_OR)
            right = self._parse_bool_and()
            left = BoolOp('||', [left, right])
        return left

    def _parse_bool_and(self) -> Expr:
        left = self._parse_bool_not()
        while self._at(_TK_AND):
            self._consume(_TK_AND)
            right = self._parse_bool_not()
            left = BoolOp('&&', [left, right])
        return left

    def _parse_bool_not(self) -> Expr:
        if self._at(_TK_NOT):
            self._consume(_TK_NOT)
            return UnaryOp('!', self._parse_bool_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Expr:
        left = self._parse_primary()
        _cmp = {_TK_EQ: '==', _TK_NEQ: '!=', _TK_LT: '<',
                _TK_GT: '>', _TK_LE: '<=', _TK_GE: '>='}
        if self._peek()[0] in _cmp:
            op = _cmp[self._consume()[0]]
            right = self._parse_primary()
            return Compare(op, left, right)
        return left

    def _parse_primary(self) -> Expr:
        if self._at(_TK_IDENT):
            return Sig(self._consume(_TK_IDENT)[1])
        if self._at(_TK_INT):
            return Const(int(self._consume(_TK_INT)[1]))
        if self._at(_TK_LPAREN):
            self._consume(_TK_LPAREN)
            node = self._parse_bool_or()
            self._consume(_TK_RPAREN)
            return node
        raise SyntaxError(f"Unexpected token {self._peek()}")


def parse_sva(s: str) -> Seq:
    """Parse an SVA sequence string into a :class:`Seq` builder object.

    Supported syntax::

        "valid ##1 ready"           # valid then ready one cycle later
        "req ##[1:3] ack"           # req then ack within 1-3 cycles
        "a[*3]"                     # a repeated 3 times
        "a[*1:$]"                   # a repeated one or more times
        "req |-> ack"               # req implies ack (overlapping)
        "req |=> ack"               # req implies ack (non-overlapping)
        "valid && !stall"           # boolean and
        "count == 5"                # comparison
    """
    tokens = _tokenize(s)
    parser = _Parser(tokens)
    ir = parser.parse_property()
    if not parser._at(_TK_EOF):
        raise SyntaxError(f"Unexpected token after expression: {parser._peek()}")
    return Seq(ir)
