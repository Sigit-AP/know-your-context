/* Error handling by return code and errno. */
#include <errno.h>
#include <string.h>

int parse_positive(const char *text, int *out) {
  if (text == 0 || out == 0) return -EINVAL;
  long value = 0;
  if (*text == '-') return -ERANGE;
  while (*text) {
    if (*text < '0' || *text > '9') return -EINVAL;
    value = value * 10 + (*text - '0');
    text++;
  }
  *out = (int)value;
  return 0;
}

const char *explain(int code) { return strerror(-code); }
