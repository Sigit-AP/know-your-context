// Custom types: records and unions.
pub type Point {
  Point(x: Int, y: Int)
}

pub type Shape {
  Circle(radius: Float)
  Rect(width: Float, height: Float)
}

pub type Tree(a) {
  Leaf(a)
  Branch(Tree(a), Tree(a))
}

pub fn area(shape: Shape) -> Float {
  case shape {
    Circle(r) -> 3.14159 *. r *. r
    Rect(w, h) -> w *. h
  }
}
