// try/catch/finally, throw as expression, custom errors.
package demo

class ConfigError(val key: String) : Exception("missing config key: $key")

fun readConfig(map: Map<String, String>, key: String): String {
    try {
        return map[key] ?: throw ConfigError(key)
    } catch (e: ConfigError) {
        throw ConfigError("re:${e.key}")
    } finally {
        map.size
    }
}

fun safeRead(map: Map<String, String>, key: String): String? =
    try {
        readConfig(map, key)
    } catch (e: ConfigError) {
        null
    }
