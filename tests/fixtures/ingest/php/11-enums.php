<?php
// Backed enums with methods and interfaces.
interface HasLabel
{
    public function label(): string;
}

enum Level: int implements HasLabel
{
    case Low = 1;
    case High = 2;

    public function label(): string
    {
        return match ($this) {
            Level::Low => "low",
            Level::High => "high",
        };
    }
}
