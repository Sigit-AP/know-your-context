// Interfaces and structural satisfaction.
package demo

type Shape interface {
	Area() float64
	Name() string
}

type Named interface {
	Name() string
}

type Circle struct {
	Radius float64
}

func (c Circle) Area() float64 {
	return 3.14159 * c.Radius * c.Radius
}

func (c Circle) Name() string {
	return "circle"
}

func TotalArea(shapes []Shape) float64 {
	total := 0.0
	for _, s := range shapes {
		total += s.Area()
	}
	return total
}
