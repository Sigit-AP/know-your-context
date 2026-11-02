## Tuples, named tuples and destructuring.
type Pair = tuple
  first: int
  second: string

proc split(text: string): Pair =
  Pair(first: text.len, second: text)

proc useSplit(text: string): int =
  let p = split(text)
  p.first
