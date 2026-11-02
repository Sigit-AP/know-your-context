# switch with regex and wildcard matching.
function Get-Classified {
    param([string]$Code)
    switch -Regex ($Code) {
        "^0" { "zero"; break }
        "^[12]$" { "small"; break }
        "^\d+$" { "numeric"; break }
        default { "other" }
    }
}

function Get-Direction {
    param([string]$Name)
    switch -Wildcard ($Name) {
        "north*" { "N"; break }
        "south*" { "S"; break }
        default { "?" }
    }
}
