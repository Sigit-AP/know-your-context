// Rich patterns: guards, bindings and destructuring.
pub enum Event {
    Key(char),
    Click { x: i32, y: i32 },
    Resize(u32, u32),
}

pub fn classify(event: &Event) -> u8 {
    match event {
        Event::Key(c) if c.is_alphabetic() => 1,
        Event::Key(_) => 2,
        Event::Click { x, y } if x == y => 3,
        Event::Click { .. } => 4,
        Event::Resize(w, _) if *w > 100 => 5,
        Event::Resize(..) => 6,
    }
}

pub fn unwrap_or_zero(value: Option<i32>) -> i32 {
    if let Some(v) = value { v } else { 0 }
}
