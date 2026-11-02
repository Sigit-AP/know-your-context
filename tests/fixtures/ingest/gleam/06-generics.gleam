// Generic functions over opaque types.
pub type Box(a) {
  Box(value: a)
}

pub fn identity(value: a) -> a {
  value
}

pub fn unwrap(box: Box(a)) -> a {
  box.value
}

pub fn first(items: List(a)) -> Result(a, Nil) {
  case items {
    [] -> Error(Nil)
    [head, ..] -> Ok(head)
  }
}
