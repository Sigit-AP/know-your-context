/* Typedef forms: alias, anonymous struct, function pointer. */
typedef unsigned long size_alias_t;

typedef struct {
  int width;
  int height;
} rect_t;

typedef int (*comparer_t)(const void *, const void *);

typedef enum { OFF = 0, ON = 1 } switch_t;

rect_t unit_square(void) {
  rect_t r = {1, 1};
  return r;
}
