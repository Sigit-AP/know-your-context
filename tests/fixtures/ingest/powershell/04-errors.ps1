# try/catch/finally, throw and typed catch.
class ConfigError {
    [string]$Key

    ConfigError([string]$key) {
        $this.Key = $key
    }

    [string] Message() {
        return "missing config key: $($this.Key)"
    }
}

function Read-Config {
    param(
        [hashtable]$Map,
        [string]$Key
    )
    try {
        if (-not $Map.ContainsKey($Key)) {
            throw [ConfigError]::new($Key)
        }
        return $Map[$Key]
    }
    catch [ConfigError] {
        return $null
    }
    catch {
        throw
    }
    finally {
        # no resources held
    }
}
