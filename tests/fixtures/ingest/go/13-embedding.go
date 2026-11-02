// Struct embedding and promoted methods.
package demo

type Logger struct {
	Prefix string
}

func (l Logger) Log(msg string) string {
	return l.Prefix + ": " + msg
}

type Service struct {
	Logger
	Name string
}

func (s Service) Describe() string {
	return s.Log(s.Name)
}
