-- Call expressions parking unresolved refs.
local function helper(value)
  return value * 2
end

function run(seed)
  local total = helper(seed)
  print(total)
  return helper(total)
end
