"""Inheritance, super calls and overriding."""


class Animal:
    def speak(self):
        return "..."


class Dog(Animal):
    def speak(self):
        base = super().speak()
        return base + " woof"


class Puppy(Dog):
    pass
