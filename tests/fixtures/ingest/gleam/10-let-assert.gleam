// let assert and use expressions.
pub fn unwrap_some(value: Result(Int, Nil)) -> Int {
  let assert Ok(x) = value
  x
}

pub fn head_or_zero(items: List(Int)) -> Int {
  let assert [head, ..] = items
  head
}
