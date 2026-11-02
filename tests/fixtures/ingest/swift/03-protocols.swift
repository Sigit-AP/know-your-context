// Protocols with requirements and extensions.
protocol Shape {
    var name: String { get }
    func area() -> Double
}

extension Shape {
    func describe() -> String {
        return "area=\(area())"
    }
}

class Circle: Shape {
    let radius: Double = 1.0
    let name: String = "circle"

    func area() -> Double {
        return 3.14159 * radius * radius
    }
}
