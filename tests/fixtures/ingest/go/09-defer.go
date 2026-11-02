// defer, panic and recover.
package demo

import "fmt"

func guarded(fn func() error) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("panic: %v", r)
		}
	}()
	return fn()
}

func risky() error {
	panic("boom")
}
