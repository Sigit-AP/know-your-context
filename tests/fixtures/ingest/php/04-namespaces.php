<?php
// Namespaces and use imports (incl. aliasing and group use).
namespace Demo\App;

use Vendor\Pkg\Thing;
use Vendor\Pkg\Other as Alias;
use Vendor\Group\{First, Second};
use function Vendor\fn\helper;

class Boot
{
    public function run(): string
    {
        return Thing::name() . helper();
    }
}
