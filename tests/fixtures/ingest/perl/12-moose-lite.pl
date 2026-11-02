# Minimal accessor OO without frameworks.
package Demo::Point;

use strict;
use warnings;

sub new {
    my ($class, $x, $y) = @_;
    return bless { x => $x // 0, y => $y // 0 }, $class;
}

sub x { return shift->{x} }
sub y { return shift->{y} }

sub move {
    my ($self, $dx, $dy) = @_;
    $self->{x} += $dx;
    $self->{y} += $dy;
    return $self;
}

1;
