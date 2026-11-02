// Lambdas, higher-order functions and receivers.
package demo

fun makeCounter(start: Int): (Int) -> Int {
    var count = start
    return { amount ->
        count += amount
        count
    }
}

fun <T, R> List<T>.mapPairs(fn: (T) -> T): List<T> = map(fn)

val doubler: (Int) -> Int = { it * 2 }

fun applyTwice(fn: (Int) -> Int, x: Int): Int = fn(fn(x))
