// Closures, captures and trailing syntax.
func makeCounter(_ start: Int) -> (Int) -> Int {
    var count = start
    return { amount in
        count += amount
        return count
    }
}

func applyTwice(_ fn: (Int) -> Int, _ x: Int) -> Int {
    return fn(fn(x))
}

func doubled(_ values: [Int]) -> [Int] {
    return values.map { $0 * 2 }
}
