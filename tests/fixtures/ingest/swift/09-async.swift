// async/await and Task-based concurrency.
func fetchOne(_ url: String) async -> String {
    return url
}

func fetchAll(_ urls: [String]) async -> [String] {
    var out: [String] = []
    for url in urls {
        out.append(await fetchOne(url))
    }
    return out
}

func run() async throws -> Int {
    let results = await fetchAll(["a", "b"])
    return results.count
}
