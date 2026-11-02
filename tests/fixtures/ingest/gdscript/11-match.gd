## match patterns with bindings and guards.
extends RefCounted


func classify(value: Variant) -> String:
	match value:
		0:
			return "zero"
		1, 2:
			return "small"
		[x] when x is int:
			return "single-int"
		{"name": var name}:
			return name
		_:
			return "other"
