// Structs, enums and methods.
module demo

pub struct Point {
mut:
	x int
	y int
}

enum Colour {
	red
	green
	blue
}

fn (mut p Point) move(dx int, dy int) {
	p.x += dx
	p.y += dy
}

fn (p Point) origin() bool {
	return p.x == 0 && p.y == 0
}
