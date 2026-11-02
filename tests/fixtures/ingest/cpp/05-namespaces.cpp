// Named, nested and anonymous namespaces.
namespace geometry {
struct Vec2 {
  double x;
  double y;
};

double Length(const Vec2 &v) { return v.x * v.x + v.y * v.y; }

namespace detail {
int Scale(int v) { return v * 2; }
}  // namespace detail
}  // namespace geometry

namespace {
int Hidden() { return 42; }
}  // namespace
