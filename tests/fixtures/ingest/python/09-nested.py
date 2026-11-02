"""Nested functions, closures and factories."""


def make_counter(start):
    count = start

    def step(amount=1):
        return count + amount

    def reset():
        return start

    return step, reset


def compose(f, g):
    def composed(x):
        return f(g(x))

    return composed
