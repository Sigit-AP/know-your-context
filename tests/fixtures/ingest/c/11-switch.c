/* Switch, goto and labelled statements. */
int classify(int code) {
  switch (code) {
    case 0:
      return 1;
    case 1:
    case 2:
      return 2;
    default:
      goto unknown;
  }
unknown:
  return -1;
}

int retry_loop(int attempts) {
  int i = 0;
again:
  if (++i < attempts) goto again;
  return i;
}
