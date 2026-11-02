// Exceptions: try/on/catch/finally and custom errors.
class ConfigError implements Exception {
  final String key;

  ConfigError(this.key);

  @override
  String toString() => 'missing config key: $key';
}

String readConfig(Map<String, String> map, String key) {
  final value = map[key];
  if (value == null) {
    throw ConfigError(key);
  }
  return value;
}

String? readSafe(Map<String, String> map, String key) {
  try {
    return readConfig(map, key);
  } on ConfigError catch (e) {
    print(e);
    return null;
  } catch (_) {
    rethrow;
  } finally {
    // no resources held
  }
}
