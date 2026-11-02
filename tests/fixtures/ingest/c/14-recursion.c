/* Direct and mutual recursion. */
int factorial(int n) { return n <= 1 ? 1 : n * factorial(n - 1); }

int is_odd(int n);
int is_even(int n) { return n == 0 ? 1 : is_odd(n - 1); }
int is_odd(int n) { return n == 0 ? 0 : is_even(n - 1); }
