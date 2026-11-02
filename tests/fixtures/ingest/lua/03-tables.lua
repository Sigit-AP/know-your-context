-- Tables as modules and namespaces.
local M = {}

M.VERSION = "1.2.3"

function M.greet(name)
  return "hello, " .. name
end

function M.farewell(name)
  return "bye, " .. name
end

return M
