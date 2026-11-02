<?php
// Interfaces and traits.
interface Shape
{
    public function area(): float;
}

trait Named
{
    public function name(): string
    {
        return static::class;
    }
}

class Circle implements Shape
{
    use Named;

    public function __construct(private float $radius)
    {
    }

    public function area(): float
    {
        return 3.14159 * $this->radius ** 2;
    }
}
