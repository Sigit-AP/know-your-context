// Closures and function values.
module demo

fn make_counter(start int) fn (int) int {
	mut count := start
	return fn [mut count] (amount int) int {
		count += amount
		return count
	}
}

pub fn use_counter() int {
	c := make_counter(10)
	return c(1)
}
