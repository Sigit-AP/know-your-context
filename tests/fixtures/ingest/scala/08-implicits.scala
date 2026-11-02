// given/using and extension methods (Scala 3).
package demo

given Ordering[String] with
  def compare(a: String, b: String): Int = a.compareTo(b)

extension (s: String) def shout: String = s.toUpperCase

def render[A](value: A)(using Ordering[A]): String = value.toString
