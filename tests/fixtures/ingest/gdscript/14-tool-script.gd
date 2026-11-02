## @tool scripts with exported vars.
@tool
extends EditorScript

@export var tile_size := 16
@export_range(0, 100) var density := 50


func _run() -> void:
	print("tile_size=%d density=%d" % [tile_size, density])
