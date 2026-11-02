// Custom computation expression builder.
module Demo.CE

type MaybeBuilder() =
    member _.Bind(value, fn) = Option.bind fn value
    member _.Return(value) = Some value
    member _.ReturnFrom(value) = value

let maybe = MaybeBuilder()

let wrapValue (x: int) =
    maybe {
        return x
    }
