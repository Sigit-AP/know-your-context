<?php
// Basic functions with types and defaults.
function add(int $a, int $b): int
{
    return $a + $b;
}

function greet(string $name, string $greeting = "hello"): string
{
    return "$greeting, $name";
}

function variadic(int $first, int ...$rest): int
{
    return array_sum([$first, ...$rest]);
}
