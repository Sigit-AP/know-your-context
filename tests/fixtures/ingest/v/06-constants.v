// Const blocks and module-level mut state.
module demo

const limit = 100

const (
	name_a = 'a'
	name_b = 'b'
)

pub fn describe() string {
	return name_a + name_b
}
