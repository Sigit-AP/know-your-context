// Classes, case classes and objects.
package demo

class Point(var x: Int = 0, var y: Int = 0) {
  def move(dx: Int, dy: Int): Point = {
    x += dx
    y += dy
    this
  }

  def origin: Boolean = x == 0 && y == 0
}

case class User(name: String, tags: List[String] = Nil)

object Registry {
  private var counter = 0

  def next(): Int = {
    counter += 1
    counter
  }
}
