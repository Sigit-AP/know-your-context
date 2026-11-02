// Generic functions and types (Go 1.18+).
package demo

func Identity[T any](value T) T {
	return value
}

type Box[T any] struct {
	value T
}

func (b Box[T]) Unwrap() T {
	return b.value
}

func Map[T, U any](items []T, fn func(T) U) []U {
	out := make([]U, len(items))
	for i, item := range items {
		out[i] = fn(item)
	}
	return out
}
