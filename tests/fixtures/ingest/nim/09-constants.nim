## const, let and var at module scope.
const Limit = 100
const Names = ["a", "b"]

var counter = 0

proc next(): int =
  counter += 1
  counter

let defaultName = "demo"
