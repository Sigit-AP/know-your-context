// Free functions with overloads and defaults.
int add(int a, int b) { return a + b; }

double add(double a, double b) { return a + b; }

int clamp(int value, int low = 0, int high = 100) {
  if (value < low) return low;
  if (value > high) return high;
  return value;
}

int use_add() { return add(1, 2) + clamp(5); }
