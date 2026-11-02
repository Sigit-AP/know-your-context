## Signals as event wiring.
extends Node

signal health_changed(new_value)
signal died

var health := 10


func take_damage(amount: int) -> void:
	health -= amount
	health_changed.emit(health)
	if health <= 0:
		died.emit()


func _ready() -> void:
	died.connect(_on_died)


func _on_died() -> void:
	queue_free()
