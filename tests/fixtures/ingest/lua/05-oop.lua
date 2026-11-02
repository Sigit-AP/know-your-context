-- Prototype-based OOP with metatables.
local Point = {}
Point.__index = Point

function Point.new(x, y)
  local self = setmetatable({}, Point)
  self.x = x or 0
  self.y = y or 0
  return self
end

function Point.move(self, dx, dy)
  self.x = self.x + dx
  self.y = self.y + dy
  return self
end

function Point.origin(self)
  return self.x == 0 and self.y == 0
end

return Point
