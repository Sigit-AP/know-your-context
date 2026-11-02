/* Struct, union and enum declarations. */
struct Point {
  int x;
  int y;
};

union Number {
  int i;
  double d;
};

enum Colour { RED, GREEN, BLUE };

struct Point origin(void) {
  struct Point p = {0, 0};
  return p;
}
