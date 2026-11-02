# eval/die error handling and custom exception objects.
use strict;
use warnings;

sub read_config {
    my ($map, $key) = @_;
    die "missing config key: $key" unless exists $map->{$key};
    return $map->{$key};
}

sub read_safe {
    my ($map, $key) = @_;
    my $value = eval { read_config($map, $key) };
    if (my $err = $@) {
        return undef;
    }
    return $value;
}

sub checked {
    my ($map, $key) = @_;
    my $value = read_config($map, $key);
    die "empty value" unless length $value;
    return $value;
}

1;
