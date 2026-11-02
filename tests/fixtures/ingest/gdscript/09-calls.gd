## Call expressions.
extends RefCounted


func helper(value: int) -> int:
	return value * 2


func run(seed: int) -> int:
	var total := helper(seed)
	return helper(total)
