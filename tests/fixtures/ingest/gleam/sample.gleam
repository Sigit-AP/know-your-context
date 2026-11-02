import gleam/io
import gleam/string

pub type Config {
  Config(size: Int)
}

pub fn greet(name: String) -> Nil {
  helper(name)
}

fn helper(x: String) -> Nil {
  io.println(x)
}
