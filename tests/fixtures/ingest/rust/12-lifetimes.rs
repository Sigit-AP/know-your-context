// Explicit lifetimes on functions and impls.
pub struct Cursor<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }

    pub fn next_byte(&mut self) -> Option<u8> {
        let byte = *self.data.get(self.pos)?;
        self.pos += 1;
        Some(byte)
    }
}

pub fn longest<'a>(a: &'a str, b: &'a str) -> &'a str {
    if a.len() >= b.len() { a } else { b }
}
