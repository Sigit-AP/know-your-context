// Traits, default methods and impl blocks.
pub trait Shape {
    fn area(&self) -> f64;

    fn describe(&self) -> String {
        format!("area={}", self.area())
    }
}

pub struct Circle {
    pub radius: f64,
}

impl Shape for Circle {
    fn area(&self) -> f64 {
        3.14159 * self.radius * self.radius
    }
}

pub fn total_area(shapes: &[&dyn Shape]) -> f64 {
    shapes.iter().map(|s| s.area()).sum()
}
