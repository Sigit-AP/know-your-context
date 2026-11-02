// Extension functions and infix.
package demo

fun String.shout(): String = uppercase()

infix fun Int.upto(other: Int): IntRange = this..other

fun <T> T.applyIf(cond: Boolean, fn: T.() -> T): T = if (cond) fn() else this
