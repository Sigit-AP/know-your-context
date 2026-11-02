// init functions and blank identifiers.
package demo

import "os"

var env string

func init() {
	env = os.Getenv("DEMO_ENV")
}

func init() {
	if env == "" {
		env = "dev"
	}
}

func Env() string {
	return env
}
