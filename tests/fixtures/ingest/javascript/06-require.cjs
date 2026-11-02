// CommonJS require() imports (regex-driven rule).
const fs = require("fs");
const path = require("path");
const local = require("./local.cjs");

function load(name) {
  return local.lookup(name, path.sep);
}

module.exports = { load, fs };
