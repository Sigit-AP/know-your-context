// Error values, wrapping and custom error types.
package demo

import (
	"errors"
	"fmt"
)

var ErrMissing = errors.New("missing config key")

type ConfigError struct {
	Key string
}

func (e *ConfigError) Error() string {
	return fmt.Sprintf("missing config key: %s", e.Key)
}

func ReadConfig(m map[string]string, key string) (string, error) {
	value, ok := m[key]
	if !ok {
		return "", &ConfigError{Key: key}
	}
	return value, nil
}

func MustRead(m map[string]string, key string) string {
	value, err := ReadConfig(m, key)
	if err != nil {
		panic(fmt.Errorf("wrap: %w", err))
	}
	return value
}
