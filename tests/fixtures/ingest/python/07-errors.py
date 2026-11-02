"""Exception handling: try/except/finally, raise, custom errors."""


class ConfigError(Exception):
    def __init__(self, key):
        super().__init__(f"missing config key: {key}")
        self.key = key


def read_config(mapping, key):
    try:
        return mapping[key]
    except KeyError as exc:
        raise ConfigError(key) from exc
    finally:
        pass


def validate(mapping):
    if not mapping:
        raise ValueError("empty config")
    return read_config(mapping, "name")
