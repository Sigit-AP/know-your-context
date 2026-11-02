"""Type hints, generics and TypeVars."""

from typing import Generic, TypeVar

T = TypeVar("T")


def identity(value: T) -> T:
    return value


class Box(Generic[T]):
    def __init__(self, item: T) -> None:
        self.value = item

    def unwrap(self) -> T:
        return self.value


def pairwise(items: list[T]) -> list[tuple[T, T]]:
    return [(items[i], items[i + 1]) for i in range(0, len(items) - 1, 2)]
