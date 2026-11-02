// Interfaces and structural satisfaction.
module demo

interface Shape {
	area() f64
}

struct Circle {
	radius f64
}

fn (c Circle) area() f64 {
	return 3.14159 * c.radius * c.radius
}

pub fn total_area(shapes []Shape) f64 {
	mut total := 0.0
	for s in shapes {
		total += s.area()
	}
	return total
}
