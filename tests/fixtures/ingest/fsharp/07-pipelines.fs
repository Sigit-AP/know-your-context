// Pipe and composition operators.
module Demo.Pipelines

let doubled (values: int list) : int list =
    values |> List.map (fun x -> x * 2)

let sumEven (values: int list) : int =
    values |> List.filter (fun x -> x % 2 = 0) |> List.sum

let composed: int -> int =
    ((+) 1) >> ((*) 2) >> ((-) 3)
