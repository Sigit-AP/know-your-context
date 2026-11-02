// File-level constants, typealiases and access control.
public let version = "1.2.3"

let limit = 100

public typealias Handler = (Int) -> String

internal func configured() -> Bool {
    return limit > 0
}

public func describe() -> String {
    return version
}
