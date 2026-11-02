-- pcall/xpcall and error objects.
local function read_config(map, key)
  local value = map[key]
  if value == nil then
    error("missing config key: " .. key, 2)
  end
  return value
end

local function read_safe(map, key)
  local ok, value = pcall(read_config, map, key)
  if not ok then
    return nil
  end
  return value
end

local function guarded(fn)
  return xpcall(fn, debug.traceback)
end
