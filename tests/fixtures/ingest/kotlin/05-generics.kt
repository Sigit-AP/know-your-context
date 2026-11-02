// Generic functions, classes and variance.
package demo

fun <T> identity(value: T): T = value

class Box<T : Any>(private val item: T) {
    fun unwrap(): T = item
}

fun <T> first(items: List<T>): T? = items.firstOrNull()

interface Producer<out T> {
    fun produce(): T
}
