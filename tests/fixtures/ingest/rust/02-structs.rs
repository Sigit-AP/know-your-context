// Structs: named, tuple and unit.
#[derive(Debug, Clone)]
pub struct Point {
    pub x: i32,
    pub y: i32,
}

pub struct Pair(i32, i32);

pub struct Marker;

impl Point {
    pub fn new(x: i32, y: i32) -> Self {
        Self { x, y }
    }

    pub fn origin(&self) -> bool {
        self.x == 0 && self.y == 0
    }
}
