# Basic subroutines with signatures and prototypes.
use strict;
use warnings;

sub add {
    my ($a, $b) = @_;
    return $a + $b;
}

sub greet {
    my ($name, $greeting) = @_;
    $greeting //= "hello";
    return "$greeting, $name";
}

sub variadic {
    my ($first, @rest) = @_;
    return scalar @rest;
}
