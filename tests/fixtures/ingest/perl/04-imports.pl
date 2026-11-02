# use/require forms: pragmas, modules with args, version pins.
use strict;
use warnings;
use JSON::PP ();
use List::Util qw(sum max);
require Carp;
require Local::Helper;

sub boot {
    return sum(1, 2);
}

1;
