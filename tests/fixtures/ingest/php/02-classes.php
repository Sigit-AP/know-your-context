<?php
// Classes with properties, constructor promotion and statics.
class Point
{
    public function __construct(
        private int $x = 0,
        private int $y = 0,
    ) {
    }

    public function move(int $dx, int $dy): self
    {
        $this->x += $dx;
        $this->y += $dy;
        return $this;
    }

    public function isOrigin(): bool
    {
        return $this->x === 0 && $this->y === 0;
    }

    public static function build(): self
    {
        return new self();
    }
}
