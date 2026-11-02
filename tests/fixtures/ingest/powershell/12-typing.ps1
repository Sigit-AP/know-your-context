# Type accelerators, generics and casts.
function New-StringMap {
    $map = [System.Collections.Generic.Dictionary[string, int]]::new()
    return $map
}

function Convert-ToIntList {
    param([string[]]$Items)
    $list = [System.Collections.Generic.List[int]]::new()
    foreach ($item in $Items) {
        $list.Add([int]$item)
    }
    return $list
}
