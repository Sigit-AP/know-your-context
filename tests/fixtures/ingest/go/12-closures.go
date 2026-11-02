// Closures and function values.
package demo

func makeCounter(start int) func(int) int {
	count := start
	return func(amount int) int {
		count += amount
		return count
	}
}

func compose(f, g func(int) int) func(int) int {
	return func(x int) int {
		return f(g(x))
	}
}
