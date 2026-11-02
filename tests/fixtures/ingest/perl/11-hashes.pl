# Hash slices, exists/delete and tied-style access.
use strict;
use warnings;

sub pick {
    my ($map, @keys) = @_;
    return @{$map}{@keys};
}

sub without_keys {
    my ($map, @drop) = @_;
    my %copy = %{$map};
    delete @copy{@drop};
    return \%copy;
}

1;
