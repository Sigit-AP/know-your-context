// Nested modules and values.
module Demo.Structure

let version = "1.2.3"

module Inner =
    let limit = 100

    let describe () = sprintf "limit=%d" limit

module Deep =
    module Deeper =
        let value = 1

let topLevel () = Inner.describe ()
