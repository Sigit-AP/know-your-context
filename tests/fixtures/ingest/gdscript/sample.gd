extends Node
class_name Greeter

const MAX := 42
var speed := 1.0

func greet(who: String) -> void:
    helper(who)

func helper(x: String) -> void:
    print(x)
