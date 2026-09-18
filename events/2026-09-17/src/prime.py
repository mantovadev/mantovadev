#!/usr/bin/env python3
from math import sqrt


def is_prime(n):
    for j in range(3, int(sqrt(n)) + 1, 2):
        if n % j == 0:
            return False
    return True


n, i = 1, 1  # il 2 è già contato: si parte da 3, solo dispari
while n < 1_000_000:
    i += 2
    if is_prime(i):
        n += 1
print("Il milionesimo primo è", i)
