// Constants, iota enums and package-level vars.
package demo

const Limit = 100

const (
	LevelLow = iota
	LevelHigh
)

var DefaultName = "demo"
var counter int

func Next() int {
	counter++
	return counter
}
