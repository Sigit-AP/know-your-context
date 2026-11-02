// Result-based error handling.
pub type ConfigError {
  Missing(String)
  Invalid(String)
}

pub fn read_config(
  map: List(#(String, String)),
  key: String,
) -> Result(String, ConfigError) {
  case list_find(map, key) {
    Ok(value) -> Ok(value)
    Error(Nil) -> Error(Missing(key))
  }
}

fn list_find(map: List(#(String, String)), key: String) -> Result(String, Nil) {
  case map {
    [] -> Error(Nil)
    [#(k, v), ..] if k == key -> Ok(v)
    [_, ..rest] -> list_find(rest, key)
  }
}

pub fn read_or(map: List(#(String, String)), key: String, default: String) -> String {
  case read_config(map, key) {
    Ok(value) -> value
    Error(_) -> default
  }
}
