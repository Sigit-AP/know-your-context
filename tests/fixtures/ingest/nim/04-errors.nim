## Exceptions: raise, try/except/finally, custom errors.
type ConfigError = object of CatchableError

proc readConfig(m: Table[string, string], key: string): string =
  if key notin m:
    raise newException(ConfigError, "missing: " & key)
  m[key]

proc safeRead(m: Table[string, string], key: string): string =
  try:
    readConfig(m, key)
  except ConfigError:
    ""
  except CatchableError:
    "other"
  finally:
    discard
