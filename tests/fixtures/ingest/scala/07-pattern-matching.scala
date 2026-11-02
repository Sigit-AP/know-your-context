// Match expressions with guards and extractors.
package demo

sealed trait Msg

case class Quit() extends Msg

case class Move(x: Int, y: Int) extends Msg

def classify(msg: Msg): String =
  msg match {
    case Quit()                => "quit"
    case Move(x, y) if x == y  => "diagonal"
    case Move(_, _)            => "move"
  }

def describe(value: Any): String =
  value match {
    case s: String  => s"string:${s.length}"
    case i: Int     => s"int:$i"
    case head :: tl => s"cons:${tl.length}"
    case _          => "unknown"
  }
