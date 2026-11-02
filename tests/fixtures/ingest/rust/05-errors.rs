// Result-based error handling and custom error types.
use std::fmt;

#[derive(Debug)]
pub enum ConfigError {
    Missing(String),
    Invalid(String),
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConfigError::Missing(key) => write!(f, "missing: {key}"),
            ConfigError::Invalid(key) => write!(f, "invalid: {key}"),
        }
    }
}

impl std::error::Error for ConfigError {}

pub fn read_config(map: &std::collections::HashMap<String, String>, key: &str) -> Result<String, ConfigError> {
    map.get(key).cloned().ok_or_else(|| ConfigError::Missing(key.to_string()))
}
