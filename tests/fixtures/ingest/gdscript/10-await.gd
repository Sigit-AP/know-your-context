## await on signals and coroutines.
extends Node


func fetch_one(url: String) -> String:
	await get_tree().process_frame
	return url


func fetch_all(urls: Array) -> Array:
	var out := []
	for url in urls:
		out.append(await fetch_one(url))
	return out


func run() -> void:
	var results = await fetch_all(["a", "b"])
	print(results.size())
