// Virtual inheritance and overriding. */
class Shape {
 public:
  virtual ~Shape() = default;
  virtual double Area() const { return 0.0; }
};

class Circle : public Shape {
 public:
  explicit Circle(double r) : r_(r) {}
  double Area() const override { return 3.14159 * r_ * r_; }

 private:
  double r_;
};
