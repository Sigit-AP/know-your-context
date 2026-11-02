// Scala 3 enums with parameters and cases.
package demo

enum Colour(val code: Int) {
  case Red extends Colour(1)
  case Green extends Colour(2)
  case Blue extends Colour(3)
}

enum Tree[+A] {
  case Leaf(value: A)
  case Branch(left: Tree[A], right: Tree[A])
}

def depth(tree: Tree[Int]): Int =
  tree match {
    case Tree.Leaf(_)      => 1
    case Tree.Branch(l, r) => 1 + math.max(depth(l), depth(r))
  }
