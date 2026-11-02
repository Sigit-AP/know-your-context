# Command invocations parking unresolved refs.
function Get-Helper {
    param([int]$Value)
    return $Value * 2
}

function Invoke-Run {
    param([int]$Seed)
    $total = Get-Helper -Value $Seed
    return Get-Helper -Value $total
}
