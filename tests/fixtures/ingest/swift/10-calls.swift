// Call expressions parking unresolved refs.
func helper(_ value: Int) -> Int {
    return value * 2
}

func run(_ seed: Int) -> Int {
    let total = helper(seed)
    return helper(total)
}
