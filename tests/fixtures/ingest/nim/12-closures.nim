## Closures and nested procs.
proc makeCounter(start: int): proc(amount: int): int =
  var count = start
  proc step(amount: int): int =
    count += amount
    count
  result = step

proc compose(f, g: proc(x: int): int): proc(x: int): int =
  proc composed(x: int): int =
    f(g(x))
  result = composed
