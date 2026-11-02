// async/await, streams and futures.
Future<String> fetchOne(String url) async {
  return url;
}

Future<List<String>> fetchAll(List<String> urls) async {
  final out = <String>[];
  for (final url in urls) {
    out.add(await fetchOne(url));
  }
  return out;
}

Stream<int> countdown(int n) async* {
  while (n > 0) {
    yield n;
    n -= 1;
  }
}

Stream<int> tickEvery() {
  return Stream.periodic(const Duration(seconds: 1), (i) => i);
}
