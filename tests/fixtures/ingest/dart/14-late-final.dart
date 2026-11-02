// late, final and const declarations.
const int limit = 100;
final DateTime booted = DateTime(2026, 1, 1);

class Config {
  late final String name;

  void init(String value) {
    name = value.trim();
  }
}

int nextCounter = 0;

int next() => ++nextCounter;
