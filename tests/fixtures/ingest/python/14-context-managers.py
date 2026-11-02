"""Context managers and with-blocks."""


class Timer:
    def __enter__(self):
        self.started = True
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def measure():
    with Timer() as timer:
        work()
    return timer


def work():
    return None
