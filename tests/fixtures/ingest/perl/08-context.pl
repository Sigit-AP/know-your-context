# List/scalar context and wantarray.
use strict;
use warnings;

sub collected {
    my (@items) = @_;
    return wantarray ? @items : scalar @items;
}

sub gather {
    my @all = collected(1, 2, 3);
    my $count = collected(4, 5);
    return $count + @all;
}

1;
