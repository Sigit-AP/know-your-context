# Here-docs and quote operators.
use strict;
use warnings;

sub page {
    my ($title) = @_;
    return <<"HTML";
<html>
  <body>$title</body>
</html>
HTML
}

sub raw_template {
    return <<'EOT';
no $interpolation here
EOT
}

sub commands {
    return qw(alpha beta gamma);
}

1;
