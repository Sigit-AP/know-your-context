// Generic functions, types and constraints.
func identity<T>(_ value: T) -> T {
    return value
}

class Box<T: Equatable> {
    private let item: T

    init(_ item: T) {
        self.item = item
    }

    func unwrap() -> T {
        return item
    }
}

func first<T: Collection>(_ items: T) -> T.Element? {
    return items.first
}
