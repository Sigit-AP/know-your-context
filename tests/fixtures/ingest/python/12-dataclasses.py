"""Dataclasses and named tuples as real class declarations."""

from dataclasses import dataclass, field
from typing import NamedTuple


@dataclass
class User:
    name: str
    tags: list[str] = field(default_factory=list)


class Size(NamedTuple):
    width: int
    height: int


def area(size):
    return size.width * size.height
