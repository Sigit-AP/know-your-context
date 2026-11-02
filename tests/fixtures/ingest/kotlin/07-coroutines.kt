// suspend functions and coroutine builders.
package demo

import kotlin.coroutines.coroutineContext

suspend fun fetchOne(url: String): String {
    coroutineContext
    return url
}

suspend fun fetchAll(urls: List<String>): List<String> {
    val out = mutableListOf<String>()
    for (url in urls) {
        out.add(fetchOne(url))
    }
    return out
}
