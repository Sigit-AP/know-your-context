# Regex-heavy text processing.
use strict;
use warnings;

sub parse_line {
    my ($line) = @_;
    if ($line =~ /^(\w+):\s*(.+)$/) {
        return ($1, $2);
    }
    return ();
}

sub sluggify {
    my ($text) = @_;
    $text =~ s/[^a-z0-9]+/-/gi;
    $text =~ s/^-|-$//g;
    return lc $text;
}

1;
