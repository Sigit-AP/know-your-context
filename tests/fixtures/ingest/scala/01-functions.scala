// defs with defaults, varargs and multiple parameter lists.
package demo

def add(a: Int, b: Int): Int = a + b

def greet(name: String, greeting: String = "hello"): String = s"$greeting, $name"

def variadic(first: Int, rest: Int*): Int = first + rest.sum

def curried(a: Int)(b: Int): Int = a + b
