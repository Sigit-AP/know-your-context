// Classes with constructors, getters and factories.
class Point {
  int x;
  int y;

  Point({this.x = 0, this.y = 0});

  factory Point.origin() => Point();

  bool get isOrigin => x == 0 && y == 0;

  void move(int dx, int dy) {
    x += dx;
    y += dy;
  }

  static Point build() => Point(x: 1, y: 1);
}
