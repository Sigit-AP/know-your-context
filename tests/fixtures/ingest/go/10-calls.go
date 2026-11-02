// Call expressions parking unresolved refs.
package demo

func helper(value int) int {
	return value * 2
}

func run(seed int) int {
	total := helper(seed)
	return helper(total)
}
