<?php
// Module-level constants, variables and late statics.
const MAX_RETRIES = 3;
const DEFAULT_TIMEOUT = 30;

$bootedAt = null;

function booted(): string
{
    global $bootedAt;
    $bootedAt = "now";
    return "ok";
}

function retryBudget(): int
{
    return MAX_RETRIES * DEFAULT_TIMEOUT;
}
