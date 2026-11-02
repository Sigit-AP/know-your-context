// Basic function declarations.
fn add(a: i32, b: i32) i32 {
    return a + b;
}

pub fn clamp(value: i32, low: i32, high: i32) i32 {
    if (value < low) return low;
    if (value > high) return high;
    return value;
}

fn useAdd() i32 {
    return add(1, 2) + clamp(5, 0, 10);
}
