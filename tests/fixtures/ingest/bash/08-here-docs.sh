#!/bin/bash
# Here-docs and process substitution.
render_page() {
  local title="$1"
  cat <<HTML
<html>
  <body>${title}</body>
</html>
HTML
}

render_raw() {
  cat <<'EOT'
no $expansion here
EOT
}

diff_configs() {
  diff <(sort "$1") <(sort "$2")
}
