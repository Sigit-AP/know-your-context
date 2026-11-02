// Properties with custom accessors and delegation.
package demo

const val LIMIT = 100

class Config {
    var name: String = "demo"
        set(value) {
            field = value.trim()
        }

    val size: Int
        get() = name.length

    val lazyValue: Int by lazy { 42 }
}
