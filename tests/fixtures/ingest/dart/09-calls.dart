// Call expressions.
int helper(int value) => value * 2;

int run(int seed) {
  final total = helper(seed);
  return helper(total);
}
