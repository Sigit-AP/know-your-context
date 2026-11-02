# Advanced functions with ShouldProcess and validation.
function Remove-ItemSafely {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Path
    )
    if ($PSCmdlet.ShouldProcess($Path, "remove")) {
        Remove-Item -LiteralPath $Path -Force
    }
}

function Get-EvenNumbers {
    [OutputType([int[]])]
    param(
        [ValidateRange(1, 100)]
        [int]$Limit = 10
    )
    2..$Limit | Where-Object { $_ % 2 -eq 0 }
}
