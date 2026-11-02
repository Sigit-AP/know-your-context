## Inheritance chains with super calls.
extends Node


class Animal:
	func speak() -> String:
		return "..."


class Dog extends Animal:
	func speak() -> String:
		return super.speak() + " woof"


class Puppy extends Dog:
	pass
