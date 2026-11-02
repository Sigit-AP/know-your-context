// Enums with associated values and raw values.
enum Direction: String {
    case north = "N"
    case south = "S"
}

enum Message {
    case quit
    case move(x: Int, y: Int)
    case write(String)

    var weight: Int {
        switch self {
        case .quit:
            return 0
        case .move:
            return 2
        case .write(let text):
            return text.count
        }
    }
}
