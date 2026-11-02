"""A factory function named after the type it builds."""


class Widget:
    def __init__(self, size=1):
        self.size = size


def create_widget(size=1):
    return Widget(size)
