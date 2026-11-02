//// Basic functions with types.

pub fn add(a: Int, b: Int) -> Int {
  a + b
}

fn greet(name: String, greeting: String) -> String {
  greeting <> ", " <> name
}

pub fn clamp(value: Int, low: Int, high: Int) -> Int {
  case value {
    v if v < low -> low
    v if v > high -> high
    v -> v
  }
}
