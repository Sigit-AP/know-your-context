// Generator functions and iteration.
function* countdown(n) {
  while (n > 0) {
    yield n;
    n -= 1;
  }
}

function* fibonacci(limit) {
  let [a, b] = [0, 1];
  while (a < limit) {
    yield a;
    [a, b] = [b, a + b];
  }
}
