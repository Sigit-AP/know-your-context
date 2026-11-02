/* Bitfields and packed-style layout. */
struct Flags {
  unsigned ready : 1;
  unsigned error : 1;
  unsigned mode : 4;
};

struct Flags flags_default(void) {
  struct Flags f = {1, 0, 7};
  return f;
}

int flags_ready(struct Flags f) { return (int)f.ready; }
