// let-bound functions with explicit types.
module Demo.Functions

let add (a: int) (b: int) : int = a + b

let greet (name: string) (greeting: string) : string =
    sprintf "%s, %s" greeting name

let rec countdown (n: int) : int list =
    if n <= 0 then [] else n :: countdown (n - 1)

let clamp (low: int) (high: int) (value: int) : int =
    if value < low then low elif value > high then high else value
