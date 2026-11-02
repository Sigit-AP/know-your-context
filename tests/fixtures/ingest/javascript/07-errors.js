// Error handling: try/catch/finally, throw, custom error classes.
class ConfigError extends Error {
  constructor(key) {
    super(`missing config key: ${key}`);
    this.key = key;
  }
}

function readConfig(mapping, key) {
  try {
    if (!(key in mapping)) {
      throw new ConfigError(key);
    }
    return mapping[key];
  } catch (err) {
    if (err instanceof ConfigError) {
      return null;
    }
    throw err;
  } finally {
    mapping = Object.freeze(mapping);
  }
}
