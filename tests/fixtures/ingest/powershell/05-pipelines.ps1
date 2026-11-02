# Pipeline processing with process blocks.
function ConvertTo-Upper {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromPipeline = $true)]
        [string]$Text
    )
    process {
        $Text.ToUpper()
    }
}

function Measure-Total {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromPipeline = $true)]
        [int]$Value
    )
    begin {
        $script:total = 0
    }
    process {
        $script:total += $Value
    }
    end {
        $script:total
    }
}
