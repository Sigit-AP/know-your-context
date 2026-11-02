-- Structured error values via tables.
local Error = {}
Error.__index = Error

function Error.new(code, message)
  return setmetatable({ code = code, message = message }, Error)
end

function Error.is(self, code)
  return self.code == code
end

local function fail_missing(key)
  return Error.new("missing", "missing config key: " .. key)
end

return Error
