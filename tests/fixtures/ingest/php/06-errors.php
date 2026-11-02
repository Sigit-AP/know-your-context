<?php
// try/catch/finally and custom exceptions.
class ConfigError extends RuntimeException
{
    public function __construct(public readonly string $key)
    {
        parent::__construct("missing config key: $key");
    }
}

function readConfig(array $map, string $key): string
{
    try {
        return $map[$key] ?? throw new ConfigError($key);
    } catch (ConfigError $e) {
        throw new ConfigError($e->key);
    } finally {
        // no resources held
    }
}

function safeRead(array $map, string $key): ?string
{
    try {
        return readConfig($map, $key);
    } catch (ConfigError) {
        return null;
    }
}
