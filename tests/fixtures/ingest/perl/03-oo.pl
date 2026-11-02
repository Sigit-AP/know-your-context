# Classic bless-based OO with inheritance.
package Demo::Animal;

sub new {
    my ($class, %args) = @_;
    return bless {%args}, $class;
}

sub speak {
    return "...";
}

package Demo::Dog;
use parent "Demo::Animal";

sub speak {
    my ($self) = @_;
    return $self->SUPER::speak() . " woof";
}

1;
