## Engine virtual methods and lifecycle.
extends Node2D

var elapsed := 0.0


func _init() -> void:
	pass


func _ready() -> void:
	set_process(true)


func _process(delta: float) -> void:
	elapsed += delta


func _physics_process(delta: float) -> void:
	pass


func _exit_tree() -> void:
	pass
