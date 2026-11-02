"""Module-level structure: constants, variables, dunder metadata."""

__version__ = "1.2.3"
__all__ = ["DEFAULT_TIMEOUT", "Settings"]

DEFAULT_TIMEOUT = 30
retry_count = 0


class Settings:
    debug = False


def get_timeout():
    return DEFAULT_TIMEOUT
