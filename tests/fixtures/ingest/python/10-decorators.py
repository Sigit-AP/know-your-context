"""Decorators: definition, stacking and parametrized forms."""

import functools


def logged(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


def repeat(times):
    def decorator(func):
        def inner():
            return [func() for _ in range(times)]

        return inner

    return decorator


@logged
@repeat(3)
def ping():
    return "pong"
