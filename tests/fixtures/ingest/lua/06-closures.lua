-- Closures and upvalues.
local function make_counter(start)
  local count = start
  local function step(amount)
    amount = amount or 1
    count = count + amount
    return count
  end
  local function reset()
    count = start
  end
  return step, reset
end

local function compose(f, g)
  return function(x)
    return f(g(x))
  end
end
