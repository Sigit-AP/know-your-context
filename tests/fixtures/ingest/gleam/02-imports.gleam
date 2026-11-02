// Import forms: plain, aliased and selective.
import gleam/io
import gleam/list
import gleam/string as text
import gleam/result.{map, try}
import gleam/option.{type Option, Some, None}

pub fn describe(names: List(String)) -> String {
  io.println("ok")
  text.join(names, ",")
}
