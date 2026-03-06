/* Fibonacci: compute fib(n) mod 2^32. ALU-bound, minimal memory.
 * Result in x1.  n is passed in x4 (set by _start).
 */
    .section .text.init
    .globl _start
_start:
    addi x1, x0, 0         /* a = 0 */
    addi x2, x0, 1         /* b = 1 */
    li   x4, 500            /* n */
    addi x5, x0, 0         /* i = 0 */
.Lloop:
    beq  x5, x4, .Ldone
    add  x3, x1, x2        /* c = a + b */
    add  x1, x2, x0        /* a = b */
    add  x2, x3, x0        /* b = c */
    addi x5, x5, 1         /* i++ */
    j    .Lloop
.Ldone:
    .word 0x10500073        /* wfi — halt */
