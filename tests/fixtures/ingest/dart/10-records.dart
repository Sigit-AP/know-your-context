// Records and patterns (Dart 3).
(int, String) pair() => (1, 'one');

({int x, int y}) pointRecord() => (x: 0, y: 0);

String describe(Object value) {
  return switch (value) {
    int i when i < 0 => 'negative',
    int i => 'int:$i',
    [int a, int b] => 'pair:$a,$b',
    (int x, String s) => 'record:$x,$s',
    _ => 'other',
  };
}
