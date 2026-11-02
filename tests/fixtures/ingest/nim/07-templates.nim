## Templates and macros as routine definitions.
template withResource(name: untyped, body: untyped): untyped =
  block:
    let name = 1
    body

macro debug(x: untyped): untyped =
  x

proc useTemplate(): int =
  withResource(res):
    res + 1
