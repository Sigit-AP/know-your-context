// Call expressions parking unresolved refs. */
int helper(int value) { return value * 2; }

int run(int base) {
  int total = helper(base);
  return helper(total);
}
