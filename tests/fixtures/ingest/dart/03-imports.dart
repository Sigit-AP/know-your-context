// Import forms: package, dart:, relative, show/hide, as.
import 'dart:async';
import 'dart:math' as math;
import 'package:collection/collection.dart' show IterableExtension;
import 'src/local.dart' hide Internal;

Future<int> bigger(int a, int b) async {
  return math.max(a, b);
}
