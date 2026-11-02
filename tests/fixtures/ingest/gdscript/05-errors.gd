## Error handling via return codes and asserts.
extends RefCounted

enum ErrorCode { OK, MISSING, INVALID }


func read_config(map: Dictionary, key: String) -> Variant:
	assert(not map.is_empty())
	return map.get(key)


func read_safe(map: Dictionary, key: String) -> Variant:
	if not map.has(key):
		push_error("missing config key: %s" % key)
		return null
	return map[key]


func checked_run(map: Dictionary) -> ErrorCode:
	if read_safe(map, "name") == null:
		return ErrorCode.MISSING
	return ErrorCode.OK
