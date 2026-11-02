## Static functions and constants as module surface.
class_name MathUtil
extends RefCounted

const TAU_APPROX := 6.28318

static var counter := 0


static func clampf(value: float, low: float, high: float) -> float:
	return clamp(value, low, high)


static func next_id() -> int:
	counter += 1
	return counter
