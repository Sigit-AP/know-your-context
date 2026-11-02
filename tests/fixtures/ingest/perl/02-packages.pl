# package declarations as module structure.
package Demo::Util;

use strict;
use warnings;

our $VERSION = "1.00";

sub helper {
    my ($value) = @_;
    return $value * 2;
}

package Demo::App;

sub run {
    my ($seed) = @_;
    return Demo::Util::helper($seed);
}

1;
