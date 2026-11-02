// Extensions adding methods to existing types.
extension String {
    func shouted() -> String {
        return uppercased()
    }
}

extension Int {
    var squared: Int {
        return self * self
    }
}

func demo() -> String {
    return "hi".shouted()
}
