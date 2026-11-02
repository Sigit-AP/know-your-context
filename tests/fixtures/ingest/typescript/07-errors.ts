// Typed error handling and custom error classes.
class ConfigError extends Error {
  constructor(public readonly key: string) {
    super(`missing config key: ${key}`);
  }
}

function readConfig(mapping: Record<string, string>, key: string): string {
  try {
    const value = mapping[key];
    if (value === undefined) {
      throw new ConfigError(key);
    }
    return value;
  } catch (err) {
    if (err instanceof ConfigError) {
      return "";
    }
    throw err;
  }
}
