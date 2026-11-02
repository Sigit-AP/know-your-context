// Function applications parking unresolved refs.
module Demo.Calls

let helper (value: int) : int = value * 2

let run (seed: int) =
    seed |> helper |> helper
