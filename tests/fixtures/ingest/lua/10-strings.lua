-- String library patterns and long brackets.
local function sluggify(text)
  local lowered = text:lower()
  return (lowered:gsub("[^a-z0-9]+", "-"):gsub("^%-", ""):gsub("%-$", ""))
end

local function page(title)
  return ([[
<html>
  <body>%s</body>
</html>
]]):format(title)
end
