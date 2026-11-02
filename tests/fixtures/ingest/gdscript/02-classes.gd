## class_name registration with inner classes.
class_name GameCharacter
extends Node2D

var speed: float = 100.0
const MAX_HEALTH := 10


func move_by(delta: Vector2) -> void:
	position += delta * speed


func is_alive() -> bool:
	return MAX_HEALTH > 0


class InnerState:
	var ready := false

	func mark_ready() -> void:
		ready = true
