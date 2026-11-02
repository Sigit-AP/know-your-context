// Basic functions with defaults and multi-return.
module demo

fn add(a int, b int) int {
	return a + b
}

fn greet(name string, greeting string) string {
	return greeting + ', ' + name
}

fn swap(a int, b int) (int, int) {
	return b, a
}

pub fn exported() int {
	return add(1, 2)
}
