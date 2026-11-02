<?php
// Closures, arrow functions and first-class callables.
function makeCounter(int $start): Closure
{
    $count = $start;
    return function (int $amount = 1) use (&$count): int {
        $count += $amount;
        return $count;
    };
}

$double = fn(int $x): int => $x * 2;

function applyTwice(callable $fn, int $x): int
{
    return $fn($fn($x));
}
