## Typed arrays and dictionaries.
extends RefCounted

var names: Array[String] = []
var scores: Dictionary = {}


func add_name(value: String) -> void:
	names.append(value)


func total_score() -> int:
	var total := 0
	for key in scores:
		total += scores[key]
	return total
