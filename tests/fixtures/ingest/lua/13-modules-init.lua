-- init.lua package-anchor style module.
local config = require("config")
local logger = require("logger")

local M = {}

function M.start()
  logger.info("starting")
  return config.ready
end

function M.stop()
  logger.info("stopping")
end

return M
