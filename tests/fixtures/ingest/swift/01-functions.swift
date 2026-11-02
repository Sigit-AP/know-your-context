// Functions with argument labels and defaults.
func add(_ a: Int, _ b: Int) -> Int {
    return a + b
}

func greet(_ name: String, greeting: String = "hello") -> String {
    return "\(greeting), \(name)"
}

func swap2<T>(_ a: T, _ b: T) -> (T, T) {
    return (b, a)
}

func variadic(_ first: Int, _ rest: Int...) -> Int {
    return rest.reduce(first, +)
}
