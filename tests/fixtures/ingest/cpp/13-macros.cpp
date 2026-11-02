// Preprocessor constants and conditional compilation. */
#include <cstdio>

#define VERSION_MAJOR 2
#define LIKELY(x) (x)

int Report(int value) {
#ifdef NDEBUG
  return value;
#else
  return std::fprintf(stderr, "%d\n", LIKELY(value));
#endif
}
