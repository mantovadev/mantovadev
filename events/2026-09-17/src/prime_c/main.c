#include <stdio.h>
#include <math.h>

static inline int is_prime(unsigned long n) {
    unsigned long sqrt_n = (unsigned long)sqrt((double)n);
    for (unsigned long j = 3; j <= sqrt_n; j += 2) {
        if (n % j == 0) return 0;
    }
    return 1;
}

int main(void) {
    unsigned long n = 1, i = 1; // il 2 è già contato: si parte da 3, solo dispari

    while (n < 1000000) {
        i += 2;
        if (is_prime(i)) n++;
    }
    printf("Il milionesimo primo è %lu\n", i);
    return 0;
}
