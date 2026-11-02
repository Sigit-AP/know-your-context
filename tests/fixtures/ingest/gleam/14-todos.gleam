// todo and panic as control flow.
pub fn unimplemented_feature() -> Int {
  todo
}

pub fn must_be_positive(value: Int) -> Int {
  case value > 0 {
    True -> value
    False -> panic as "value must be positive"
  }
}
