// defer blocks and cleanup.
module demo

import os

pub fn read_first(path string) string {
	mut f := os.open(path) or { return '' }
	defer {
		f.close()
	}
	return f.read_line() or { '' }
}
