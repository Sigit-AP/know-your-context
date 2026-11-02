// Option/Result error propagation with or-blocks.
module demo

fn read_config(m map[string]string, key string) ?string {
	if key in m {
		return m[key]
	}
	return none
}

pub fn must_config(m map[string]string, key string) string {
	value := read_config(m, key) or { return 'default' }
	return value
}

pub fn try_config(m map[string]string, key string) ?string {
	value := read_config(m, key) or { return none }
	return value
}
