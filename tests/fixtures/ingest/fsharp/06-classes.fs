// Classes with members, properties and interfaces.
module Demo.Classes

type IShape =
    abstract Area: float
    abstract Name: string

type Circle(radius: float) =
    member _.Radius = radius

    member this.Area() = 3.14159 * radius * radius

    interface IShape with
        member this.Area = this.Area()
        member _.Name = "circle"

type PointClass() =
    let mutable x = 0
    let mutable y = 0

    member this.Move(dx: int, dy: int) =
        x <- x + dx
        y <- y + dy

    member _.Position = (x, y)

let makeCircle radius = Circle(radius)

let makePoint () = PointClass()
