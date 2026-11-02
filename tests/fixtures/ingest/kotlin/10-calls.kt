// Call expressions parking unresolved refs.
package demo

fun helper(value: Int): Int = value * 2

fun run(seed: Int): Int {
    val total = helper(seed)
    return helper(total)
}
