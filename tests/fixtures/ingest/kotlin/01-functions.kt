// Top-level functions with defaults and named args.
package demo

fun add(a: Int, b: Int): Int = a + b

fun greet(name: String, greeting: String = "hello"): String = "$greeting, $name"

fun variadic(first: Int, vararg rest: Int): Int = first + rest.sum()

tailrec fun countdown(n: Int, acc: Int = 0): Int =
    if (n == 0) acc else countdown(n - 1, acc + n)
