// Import forms: plain, aliased, dot and blank.
package demo

import (
	"fmt"
	"strings"
	alias "example.com/mod/util"
	. "example.com/mod/dot"
	_ "example.com/mod/sideeffect"
)

func Describe(values []string) string {
	return fmt.Sprintf("%s %s", strings.Join(values, ","), alias.Tag())
}
