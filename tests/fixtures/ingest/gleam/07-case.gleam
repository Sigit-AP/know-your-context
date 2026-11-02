// case with guards, spreads and alternatives.
pub type Msg {
  Quit
  Move(x: Int, y: Int)
  Write(String)
}

pub fn weight(msg: Msg) -> Int {
  case msg {
    Quit -> 0
    Move(x, y) if x == y -> 3
    Move(_, _) -> 2
    Write(text) -> string_length(text)
  }
}

fn string_length(text: String) -> Int {
  case text {
    "" -> 0
    _ -> 1
  }
}
