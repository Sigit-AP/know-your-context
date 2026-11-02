// Match with guards, lists and records.
module Demo.Patterns

type Event =
    | Key of char
    | Click of x: int * y: int
    | Resize of w: int * h: int

let classify (event: Event) : int =
    match event with
    | Key c when System.Char.IsLetter c -> 1
    | Key _ -> 2
    | Click (x, y) when x = y -> 3
    | Click _ -> 4
    | Resize (w, _) when w > 100 -> 5
    | Resize _ -> 6

let lengthOf (xs: int list) =
    List.length xs
