## Call expressions parking unresolved refs.
proc helper(value: int): int =
  value * 2

proc run(seed: int): int =
  let total = helper(seed)
  helper(total)
