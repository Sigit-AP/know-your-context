// Enhanced enums with fields and methods.
enum Level {
  low(1),
  high(2);

  final int code;

  const Level(this.code);

  bool get isHigh => this == Level.high;
}

enum Direction { north, south, east, west }
