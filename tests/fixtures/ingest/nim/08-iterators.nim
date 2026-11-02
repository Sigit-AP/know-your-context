## Iterators as resumable routines.
iterator countdown(n: int): int =
  var i = n
  while i > 0:
    yield i
    dec i

iterator chars(text: string): char =
  for c in text:
    yield c

proc collectDown(n: int): seq[int] =
  result = @[]
  for v in countdown(n):
    result.add v
