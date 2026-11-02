using namespace System.Collections

function Invoke-Greet {
    param([string]$Name)
    Write-Output (Get-Helper $Name)
}

function Get-Helper([string]$x) {
    return $x.ToUpper()
}

class Greeter {
    [string] Greet([string]$who) {
        return $who
    }
}
