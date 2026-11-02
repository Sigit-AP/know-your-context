// Import forms with aliasing.
package demo

import kotlin.collections.List
import java.util.Objects.requireNonNull
import kotlin.math.max as maximum
import java.util.ArrayList

fun bigger(a: Int, b: Int): Int {
    val xs: List<Int> = ArrayList()
    requireNonNull(xs)
    return maximum(a, b)
}
