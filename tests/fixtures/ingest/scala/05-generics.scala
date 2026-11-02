// Generic defs, classes and variance annotations.
package demo

def identity[T](value: T): T = value

class Box[T](private val item: T) {
  def unwrap: T = item
}

trait Producer[+T] {
  def produce: T
}

def first[T](items: List[T]): Option[T] = items.headOption
