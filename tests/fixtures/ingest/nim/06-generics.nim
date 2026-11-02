## Generic procs and type classes.
type Box[T] = object
  value: T

proc identity[T](value: T): T =
  value

proc unwrap[T](box: Box[T]): T =
  box.value

proc first[T](items: openArray[T]): T =
  items[0]
