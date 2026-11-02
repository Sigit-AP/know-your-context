// Abstract type members and type aliases.
package demo

trait Container {
  type Item

  def head: Item
}

type Pair[A] = (A, A)

class IntBox(value: Int) extends Container {
  type Item = Int

  def head: Int = value
}
