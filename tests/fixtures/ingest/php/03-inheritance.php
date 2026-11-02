<?php
// Inheritance, parent:: and abstract classes.
abstract class Animal
{
    abstract public function speak(): string;

    public function describe(): string
    {
        return get_class($this) . ": " . $this->speak();
    }
}

class Dog extends Animal
{
    public function speak(): string
    {
        return "woof";
    }
}

final class Puppy extends Dog
{
}
