// Generic classes, methods and bounds.
class Box<T extends Object> {
  final T item;

  Box(this.item);

  T unwrap() => item;
}

T identity<T>(T value) => value;

T? first<T>(List<T> items) => items.isEmpty ? null : items.first;

R foldLeft<T, R>(List<T> items, R seed, R Function(R, T) fn) {
  var acc = seed;
  for (final item in items) {
    acc = fn(acc, item);
  }
  return acc;
}
