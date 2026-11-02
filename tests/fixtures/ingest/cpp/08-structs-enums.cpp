// Structs, enum class and unions. */
struct Size {
  int width;
  int height;
};

enum class Level { kLow = 1, kHigh = 2 };

union Payload {
  int i;
  double d;
};

int AreaOf(const Size &s) { return s.width * s.height; }
