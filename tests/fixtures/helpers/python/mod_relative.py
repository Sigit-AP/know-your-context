"""Relative import from inside a package."""

from .sibling import tool


def boot():
    return tool
