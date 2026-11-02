// Generic functions and inline constraints.
module Demo.Generics

let identity (value: 'T) : 'T = value

let first (items: 'T list) : 'T option = List.tryHead items

let inline addGeneric a b = a + b

type Box<'T when 'T: equality>(item: 'T) =
    member _.Unwrap() = item
