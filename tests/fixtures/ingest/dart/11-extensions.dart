// Extension methods and getters on existing types.
extension StringShout on String {
  String shouted() => toUpperCase();
}

extension IntX on int {
  int get squared => this * this;
}

String demo() => 'hi'.shouted();
