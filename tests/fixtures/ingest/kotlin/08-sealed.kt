// Sealed classes, enums and exhaustive when.
package demo

sealed class Result {
    data class Ok(val value: String) : Result()
    data class Err(val message: String) : Result()
}

enum class Level(val code: Int) {
    LOW(1),
    HIGH(2),
}

fun weight(result: Result): Int =
    when (result) {
        is Result.Ok -> result.value.length
        is Result.Err -> -1
    }
