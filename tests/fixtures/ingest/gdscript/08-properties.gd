## Properties with getters/setters.
extends Resource

var health := 10:
	set(value):
		health = clampi(value, 0, 10)
	get:
		return health

var title: String = "demo":
	set = _set_title


func _set_title(value: String) -> void:
	@warning_ignore("shadowed_variable")
	var title := value.strip_edges()
