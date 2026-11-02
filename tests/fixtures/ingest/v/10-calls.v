// Call expressions parking unresolved refs.
module demo

fn helper(value int) int {
	return value * 2
}

pub fn run(seed int) int {
	total := helper(seed)
	return helper(total)
}
