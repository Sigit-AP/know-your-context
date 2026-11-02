// Traits with abstract and concrete members.
package demo

trait Shape {
  def area: Double

  def describe: String = s"area=$area"
}

trait Named {
  def name: String
}

class Circle(radius: Double) extends Shape with Named {
  def area: Double = 3.14159 * radius * radius

  def name: String = "circle"
}
