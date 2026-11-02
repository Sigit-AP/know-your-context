// lazy val, val and var at class scope.
package demo

object Config {
  val name: String = "demo"
  var debug: Boolean = false
  lazy val expensive: Int = (1 to 100).sum

  def describe: String = s"$name($debug,$expensive)"
}
