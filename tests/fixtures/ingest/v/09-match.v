// match expressions with ranges and else.
module demo

pub fn classify(code int) string {
	return match code {
		0 { 'zero' }
		1, 2 { 'small' }
		3 ... 9 { 'mid' }
		else { 'big' }
	}
}
