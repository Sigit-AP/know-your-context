<?php
// PHP 8 attributes on classes and methods.
#[Attribute(Attribute::TARGET_CLASS | Attribute::TARGET_METHOD)]
class Tag
{
    public function __construct(public string $name)
    {
    }
}

#[Tag("service")]
class Service
{
    #[Tag("entry")]
    public function handle(): void
    {
    }
}
