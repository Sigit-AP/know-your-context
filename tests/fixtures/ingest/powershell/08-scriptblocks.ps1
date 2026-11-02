# Scriptblocks, closures and invocation.
function New-Counter {
    param([int]$Start)
    $count = $Start
    return {
        param([int]$Amount = 1)
        $script:count += $Amount
        return $script:count
    }.GetNewClosure()
}

function Invoke-Twice {
    param(
        [scriptblock]$Block,
        [int]$Value
    )
    & $Block (& $Block $Value)
}
