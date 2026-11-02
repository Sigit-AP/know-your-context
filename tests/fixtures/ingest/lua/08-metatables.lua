-- Metamethods: arithmetic, comparison and call.
local Vec = {}
Vec.__index = Vec

function Vec.new(x, y)
  return setmetatable({ x = x, y = y }, Vec)
end

Vec.__add = function(a, b)
  return Vec.new(a.x + b.x, a.y + b.y)
end

Vec.__eq = function(a, b)
  return a.x == b.x and a.y == b.y
end

Vec.__tostring = function(v)
  return "(" .. v.x .. "," .. v.y .. ")"
end

return Vec
