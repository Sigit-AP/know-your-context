// Module imports with aliasing.
module demo

import os
import strings
import encoding.base64

pub fn describe() string {
	return os.getenv('X') + base64.encode('a'.bytes()).str()
}
