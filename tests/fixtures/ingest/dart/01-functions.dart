// Top-level functions with named and optional params.
int add(int a, int b) => a + b;

String greet(String name, {String greeting = 'hello'}) => '$greeting, $name';

int variadic(int first, [int second = 0]) => first + second;

int clamp(int value, int low, int high) {
  if (value < low) return low;
  if (value > high) return high;
  return value;
}
