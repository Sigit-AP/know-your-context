<?php
// Union types, nullable and intersection types (PHP 8 surface).
interface Log
{
}

class FileLog implements Log
{
}

class NullLog implements Log
{
}

function pickLog(bool $debug): FileLog|NullLog
{
    return $debug ? new FileLog() : new NullLog();
}

function find(int $id): ?string
{
    return $id > 0 ? "found" : null;
}
