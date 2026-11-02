// Methods with value and pointer receivers.
package demo

type Point struct {
	X int
	Y int
}

func (p Point) Origin() bool {
	return p.X == 0 && p.Y == 0
}

func (p *Point) Move(dx, dy int) {
	p.X += dx
	p.Y += dy
}

func NewPoint() *Point {
	return &Point{}
}
