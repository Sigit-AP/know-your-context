/* String handling with standard library calls. */
#include <string.h>

int copy_into(char *dst, const char *src, int capacity) {
  int len = (int)strlen(src);
  if (len >= capacity) return -1;
  memcpy(dst, src, (unsigned)len + 1);
  return len;
}

int starts_with(const char *text, const char *prefix) {
  return strncmp(text, prefix, strlen(prefix)) == 0;
}
