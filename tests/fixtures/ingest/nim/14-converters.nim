## Converters and result/control-flow pragmas.
converter toInt(b: bool): int =
  if b: 1 else: 0

proc asInt(b: bool): int =
  b

proc raiseOrValue(flag: bool): int {.raises: [].} =
  asInt(flag)
