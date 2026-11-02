## case/when and compile-time branches.
proc classify(code: int): string =
  case code
  of 0:
    "zero"
  of 1, 2:
    "small"
  else:
    "big"

when defined(release):
  proc buildMode(): string = "release"
else:
  proc buildMode(): string = "debug"
