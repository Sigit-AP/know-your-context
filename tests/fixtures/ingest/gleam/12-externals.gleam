// External function declarations.
@external(erlang, "lists", "reverse")
pub fn reverse(items: List(a)) -> List(a)

@external(javascript, "./ffi.mjs", "now")
pub fn now() -> Int
