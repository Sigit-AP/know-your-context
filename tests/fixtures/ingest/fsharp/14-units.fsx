// Units of measure in an .fsx script.
module Demo.Units

[<Measure>]
type m

[<Measure>]
type s

let speed (distance: float<m>) (time: float<s>) : float<m / s> =
    distance / time

let marathon: float<m> = 42195.0<m>

let describe () = speed marathon 7200.0<s>
