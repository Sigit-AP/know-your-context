# Basic functions with parameters and defaults.
function Add-Numbers {
    param(
        [int]$A,
        [int]$B
    )
    return $A + $B
}

function Get-Greeting {
    param(
        [string]$Name = "world"
    )
    return "hello, $Name"
}

function Write-Twice {
    param([string]$Text)
    Write-Output $Text
    Write-Output $Text
}
