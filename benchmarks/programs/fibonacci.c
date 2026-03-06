/* Fibonacci: compute fib(500) mod 2^32.  ALU-bound, minimal memory.
 * Result in x1 (return value).
 */

int main(void) {
    unsigned a = 0, b = 1;
    for (int i = 0; i < 500; i++) {
        unsigned c = a + b;
        a = b;
        b = c;
    }
    return (int)a;
}
