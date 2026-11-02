# map/grep/sort functional pipelines.
use strict;
use warnings;

sub evens {
    return grep { $_ % 2 == 0 } @_;
}

sub doubled {
    return map { $_ * 2 } @_;
}

sub sorted_unique {
    my %seen;
    return sort { $a <=> $b } grep { !$seen{$_}++ } @_;
}

1;
