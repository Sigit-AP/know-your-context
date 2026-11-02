// String interpolation and raw strings.
module demo

pub fn banner(name string, count int) string {
	return 'user=${name} count=${count}'
}

pub fn raw() string {
	return r'c:\no\escape'
}
