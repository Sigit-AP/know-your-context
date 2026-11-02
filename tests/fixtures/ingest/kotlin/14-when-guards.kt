// when with ranges, type checks and smart casts.
package demo

fun classify(value: Any): String =
    when (value) {
        is String -> "string:${value.length}"
        is Int -> when (value) {
            in 0..9 -> "single"
            in 10..99 -> "double"
            else -> "big"
        }
        is List<*> -> "list:${value.size}"
        else -> "unknown"
    }
