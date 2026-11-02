"""Basic function declarations."""


def add(a, b):
    return a + b


def greet(name, greeting="hello"):
    return f"{greeting}, {name}"


def variadic(first, *rest, **kwargs):
    return (first, rest, kwargs)
