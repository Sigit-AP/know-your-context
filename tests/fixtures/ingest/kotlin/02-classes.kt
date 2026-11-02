// Classes, data classes and constructors.
package demo

class Point(var x: Int = 0, var y: Int = 0) {
    fun move(dx: Int, dy: Int): Point {
        x += dx
        y += dy
        return this
    }

    fun origin(): Boolean = x == 0 && y == 0
}

data class User(val name: String, val tags: List<String> = emptyList())

object Registry {
    var counter: Int = 0

    fun next(): Int = ++counter
}
