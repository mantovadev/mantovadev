CC = cc
CFLAGS = -O3 -march=native -flto -funroll-loops -Wall -Wextra
LDLIBS = -lm

prime: main.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f prime

.PHONY: clean
