// throws/try/catch, do/catch and custom Error types.
enum ConfigError: Error {
    case missing(String)
    case invalid(String)
}

func readConfig(_ map: [String: String], key: String) throws -> String {
    guard let value = map[key] else {
        throw ConfigError.missing(key)
    }
    return value
}

func safeRead(_ map: [String: String], key: String) -> String? {
    do {
        return try readConfig(map, key: key)
    } catch ConfigError.missing(let key) {
        print("missing \(key)")
        return nil
    } catch {
        return nil
    }
}
