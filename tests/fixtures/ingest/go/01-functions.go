// Basic function declarations.
package demo

func add(a int, b int) int {
	return a + b
}

func swap(a, b int) (int, int) {
	return b, a
}

func variadic(first int, rest ...int) int {
	total := first
	for _, v := range rest {
		total += v
	}
	return total
}
