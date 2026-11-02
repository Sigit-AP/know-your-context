// Sum types and type aliases.
module demo

type Number = int | f64

type Callback = fn (int) int

pub fn weight(n Number) int {
	return match n {
		int { n }
		f64 { int(n) }
	}
}
