// Enums with payloads and exhaustive matching.
pub enum Message {
    Quit,
    Move { x: i32, y: i32 },
    Write(String),
    Colour(u8, u8, u8),
}

pub fn weight(message: &Message) -> usize {
    match message {
        Message::Quit => 0,
        Message::Move { .. } => 2,
        Message::Write(text) => text.len(),
        Message::Colour(..) => 3,
    }
}

pub type Result2<T> = Result<T, String>;
