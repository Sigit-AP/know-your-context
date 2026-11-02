// Optionals: guard, if-let, nil-coalescing and chaining.
func firstChar(_ text: String) -> Character? {
    return text.first
}

func describe(_ text: String?) -> String {
    guard let text = text, let first = text.first else {
        return "empty"
    }
    return "\(first)"
}

func lengthOrZero(_ text: String?) -> Int {
    return text?.count ?? 0
}
