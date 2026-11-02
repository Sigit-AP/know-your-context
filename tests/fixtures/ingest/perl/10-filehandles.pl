# Filehandles and lexical IO.
use strict;
use warnings;
use autodie;

sub slurp {
    my ($path) = @_;
    open my $fh, "<", $path;
    my $data = do { local $/; <$fh> };
    close $fh;
    return $data;
}

sub first_line {
    my ($path) = @_;
    open my $fh, "<", $path;
    my $line = <$fh>;
    close $fh;
    return $line;
}

1;
