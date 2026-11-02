# References: arrays, hashes, subs and nested structures.
use strict;
use warnings;

sub build_index {
    my (@items) = @_;
    my %index;
    $index{$_} = 1 for @items;
    return \%index;
}

sub apply_all {
    my ($fn, @values) = @_;
    return map { $fn->($_) } @values;
}

my $doubler = sub {
    my ($x) = @_;
    return $x * 2;
};

sub use_doubler {
    return apply_all($doubler, 1, 2, 3);
}

1;
