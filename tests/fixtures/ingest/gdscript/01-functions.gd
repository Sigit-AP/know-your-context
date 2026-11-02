## Basic function declarations with types and defaults.
extends RefCounted


func add(a: int, b: int) -> int:
	return a + b


func greet(name: String, greeting := "hello") -> String:
	return "%s, %s" % [greeting, name]


func variadic(first: int) -> int:
	return first
