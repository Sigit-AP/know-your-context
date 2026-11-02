// open/override and interface implementation.
package demo

open class Animal {
    open fun speak(): String = "..."
}

interface Walkable {
    fun walk(): String
}

class Dog : Animal(), Walkable {
    override fun speak(): String = super.speak() + " woof"
    override fun walk(): String = "walking"
}
