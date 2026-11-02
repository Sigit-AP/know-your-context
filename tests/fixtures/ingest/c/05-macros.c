/* Object-like and function-like macros (preproc_def -> constant). */
#define MAX_BUFFER 4096
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define SQUARE(x) ((x) * (x))

int shrink(int a, int b) { return MIN(a, b) + SQUARE(1); }
