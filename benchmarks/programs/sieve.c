/* Sieve of Eratosthenes: count primes below 200.  Mixed ALU + memory.
 * Result: prime count (return value) = 46.
 */

#define LIMIT 200

static int is_prime[LIMIT];

int main(void) {
    /* Mark all as prime */
    for (int i = 0; i < LIMIT; i++)
        is_prime[i] = 1;

    /* Sieve */
    for (int p = 2; p < LIMIT; p++) {
        if (!is_prime[p]) continue;
        for (int j = p + p; j < LIMIT; j += p)
            is_prime[j] = 0;
    }

    /* Count primes */
    int count = 0;
    for (int i = 2; i < LIMIT; i++)
        if (is_prime[i]) count++;
    return count;
}
