<?php
// Call expressions parking unresolved refs.
function helper(int $value): int
{
    return $value * 2;
}

function run(int $seed): int
{
    $total = helper($seed);
    return helper($total);
}
