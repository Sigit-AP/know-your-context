"""Call expressions that park unresolved refs for the resolver."""


def helper(value):
    return value * 2


def run(items):
    doubled = [helper(item) for item in items]
    total = sum(doubled)
    print(total)
    return len(doubled), total
