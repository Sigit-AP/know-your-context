/* Pointer-heavy declarations: arrays, function pointers, const. */
int sum_array(const int *values, int count) {
  int total = 0;
  for (int i = 0; i < count; i++) total += values[i];
  return total;
}

void swap(int *a, int *b) {
  int tmp = *a;
  *a = *b;
  *b = tmp;
}

int (*choose(int descending))(int, int) { return 0; }
