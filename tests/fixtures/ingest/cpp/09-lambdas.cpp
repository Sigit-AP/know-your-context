// Lambdas, auto and captures. */
#include <functional>

std::function<int(int)> MakeAdder(int amount) {
  return [amount](int x) { return x + amount; };
}

int ApplyTwice(const std::function<int(int)> &fn, int x) {
  return fn(fn(x));
}
