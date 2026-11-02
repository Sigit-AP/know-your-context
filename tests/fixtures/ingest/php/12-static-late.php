<?php
// Late static binding and static return types.
class Base
{
    public static function create(): static
    {
        return new static();
    }

    public function whoAmI(): string
    {
        return static::class;
    }
}

class Child extends Base
{
}
