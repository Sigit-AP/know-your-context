// Classes with ctor/dtor and const methods.
class Point {
 public:
  Point() : x_(0), y_(0) {}
  ~Point() = default;

  void Move(int dx, int dy) {
    x_ += dx;
    y_ += dy;
  }

  bool Origin() const { return x_ == 0 && y_ == 0; }

 private:
  int x_;
  int y_;
};

class Named {
 public:
  const char *Name() const { return "named"; }
};
