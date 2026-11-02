-- Basic function declarations and local functions.
function add(a, b)
  return a + b
end

local function clamp(value, low, high)
  if value < low then return low end
  if value > high then return high end
  return value
end

function variadic(first, ...)
  local rest = {...}
  return first, #rest
end

function useClamp(v)
  return clamp(v, 0, 10)
end
