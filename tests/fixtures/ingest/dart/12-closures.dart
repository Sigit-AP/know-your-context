// Closures and function values.
int Function(int) makeCounter(int start) {
  var count = start;
  return (int amount) {
    count += amount;
    return count;
  };
}

int applyTwice(int Function(int) fn, int x) => fn(fn(x));

final doubler = (int x) => x * 2;
