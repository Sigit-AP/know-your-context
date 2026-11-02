// Classes with stored/computed properties and deinit.
class Point {
    var x: Int = 0
    var y: Int = 0

    var isOrigin: Bool {
        return x == 0 && y == 0
    }

    func move(dx: Int, dy: Int) {
        x += dx
        y += dy
    }

    static func build() -> Point {
        return Point()
    }

    deinit {
        x = 0
    }
}
