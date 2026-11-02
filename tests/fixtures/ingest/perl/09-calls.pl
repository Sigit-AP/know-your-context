# Calls parking unresolved refs.
use strict;
use warnings;

sub helper {
    my ($value) = @_;
    return $value * 2;
}

sub run {
    my ($seed) = @_;
    my $total = helper($seed);
    return helper($total);
}

1;
