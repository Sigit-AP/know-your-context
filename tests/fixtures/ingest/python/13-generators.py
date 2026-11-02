"""Generators and iterator protocol."""


def countdown(n):
    while n > 0:
        yield n
        n -= 1


def fibonacci(limit):
    a, b = 0, 1
    while a < limit:
        yield a
        a, b = b, a + b


class Bag:
    def __iter__(self):
        return iter([])

    def __len__(self):
        return 0
