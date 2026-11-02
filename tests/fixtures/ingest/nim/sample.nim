import strutils
import tables

type Greeter = object of RootObj
  name: string

proc greet(g: Greeter, who: string) =
  echo helper(who)

proc helper(x: string): string =
  x.toUpper()

const LIMIT = 10
