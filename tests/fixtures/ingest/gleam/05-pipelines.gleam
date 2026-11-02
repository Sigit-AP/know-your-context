// Pipe chains and function composition.
import gleam/list
import gleam/string

pub fn doubled(values: List(Int)) -> List(Int) {
  values |> list.map(fn(x) { x * 2 })
}

pub fn sum_even(values: List(Int)) -> Int {
  values
  |> list.filter(fn(x) { x % 2 == 0 })
  |> list.fold(0, fn(a, b) { a + b })
}

pub fn sluggify(text: String) -> String {
  text
  |> string.lowercase
  |> string.replace(" ", "-")
}
