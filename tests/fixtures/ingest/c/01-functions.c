/* Basic function definitions and prototypes. */
static int add(int a, int b) { return a + b; }

int clamp(int value, int low, int high) {
  if (value < low) return low;
  if (value > high) return high;
  return value;
}

void noop(void) {}

int use_add(void) { return add(1, 2) + clamp(5, 0, 10); }
