## Basic procedures, funcs and default args.
proc add(a, b: int): int =
  a + b

proc greet(name: string, greeting = "hello"): string =
  greeting & ", " & name

func doubled(x: int): int =
  x * 2

proc variadic(items: varargs[string]): int =
  items.len
