# Here-strings and interpolation.
function Get-Page {
    param([string]$Title)
    return @"
<html>
  <body>$Title</body>
</html>
"@
}

function Get-RawTemplate {
    return @'
no $interpolation here
'@
}
