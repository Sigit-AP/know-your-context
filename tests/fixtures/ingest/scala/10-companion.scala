// Companion objects with apply/unapply.
package demo

class Token(val text: String)

object Token {
  def apply(text: String): Token = new Token(text.trim)

  def unapply(token: Token): Option[String] = Some(token.text)
}

def parse(raw: String): Token = Token(raw)
