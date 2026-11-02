// Basic functions, generics and lifetimes.
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn clamp(value: i32, low: i32, high: i32) -> i32 {
    if value < low {
        low
    } else if value > high {
        high
    } else {
        value
    }
}

pub fn first<'a, T>(items: &'a [T]) -> Option<&'a T> {
    items.first()
}
