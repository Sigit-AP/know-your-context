-- Varargs with table.pack and select.
local function sum(...)
  local total = 0
  for _, v in ipairs({...}) do
    total = total + v
  end
  return total
end

local function count_args(...)
  return select("#", ...)
end

local function tail(...)
  return select(2, ...)
end
