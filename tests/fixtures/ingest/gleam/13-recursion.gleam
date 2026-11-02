// Recursive definitions with accumulators.
pub fn factorial(n: Int) -> Int {
  case n {
    0 -> 1
    1 -> 1
    _ -> n * factorial(n - 1)
  }
}

pub fn sum(values: List(Int)) -> Int {
  sum_loop(values, 0)
}

fn sum_loop(values: List(Int), acc: Int) -> Int {
  case values {
    [] -> acc
    [head, ..rest] -> sum_loop(rest, acc + head)
  }
}
