// Structs with mutating methods and memberwise init.
struct Size {
    var width: Int
    var height: Int

    mutating func scale(by factor: Int) {
        width *= factor
        height *= factor
    }

    func area() -> Int {
        return width * height
    }
}

struct Pair<A, B> {
    let first: A
    let second: B
}
