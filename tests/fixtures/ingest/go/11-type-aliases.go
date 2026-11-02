// Type declarations: aliases, defined types and structs.
package demo

type Celsius = float64

type UserID int64

type Handler func(string) error

type Config struct {
	Name  string
	Limit int
}

func (u UserID) Valid() bool {
	return u > 0
}
