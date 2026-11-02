-- Coroutines as cooperative concurrency.
local function producer(n)
  return coroutine.create(function()
    for i = 1, n do
      coroutine.yield(i)
    end
  end)
end

local function collect(co)
  local out = {}
  while true do
    local ok, value = coroutine.resume(co)
    if not ok or value == nil then
      break
    end
    out[#out + 1] = value
  end
  return out
end
