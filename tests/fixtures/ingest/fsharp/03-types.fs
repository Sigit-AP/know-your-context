// Records, discriminated unions and type aliases.
module Demo.Types

type Point = { X: int; Y: int }

type Shape =
    | Circle of radius: float
    | Rect of width: float * height: float

type UserId = int64

let origin: Point = { X = 0; Y = 0 }

let area (shape: Shape) =
    match shape with
    | Circle r -> 3.14159 * r * r
    | Rect (w, h) -> w * h
