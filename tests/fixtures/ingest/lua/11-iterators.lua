-- Custom iterators and generic-for.
local function range(n)
  local i = 0
  return function()
    i = i + 1
    if i <= n then
      return i
    end
  end
end

local function keys_sorted(map)
  local keys = {}
  for k in pairs(map) do
    keys[#keys + 1] = k
  end
  table.sort(keys)
  return keys
end
