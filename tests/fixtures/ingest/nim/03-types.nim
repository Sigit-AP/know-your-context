## Object types, enums and distinct types.
type
  Point = object
    x, y: int

  Colour = enum
    red, green, blue

  UserId = distinct int64

proc newPoint(x, y: int): Point =
  Point(x: x, y: y)

proc origin(p: Point): bool =
  p.x == 0 and p.y == 0
