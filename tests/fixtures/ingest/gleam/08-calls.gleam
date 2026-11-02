// Calls parking unresolved refs.
fn helper(value: Int) -> Int {
  value * 2
}

pub fn run(seed: Int) -> Int {
  let total = helper(seed)
  helper(total)
}
