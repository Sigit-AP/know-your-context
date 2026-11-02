-- require forms: plain, assigned and parenthesized.
local json = require("json")
local util = require "util"
local sub = require("pkg.sub.mod")

function boot()
  return json.encode({}) .. util.tag()
end
