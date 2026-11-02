# Module file with exported members.
$script:Counter = 0

function Get-Counter {
    return $script:Counter
}

function Step-Counter {
    param([int]$Amount = 1)
    $script:Counter += $Amount
    return $script:Counter
}

function Reset-Counter {
    $script:Counter = 0
}

Export-ModuleMember -Function Get-Counter
Export-ModuleMember -Function Step-Counter
Export-ModuleMember -Function Reset-Counter
