// Call expressions parking unresolved refs.
fn helper(value: i32) -> i32 {
    value * 2
}

pub fn run(seed: i32) -> i32 {
    let total = helper(seed);
    helper(total)
}
