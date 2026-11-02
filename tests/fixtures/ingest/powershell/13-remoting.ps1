# Jobs and parallel-ish patterns.
function Invoke-BackgroundWork {
    param([int]$Seconds)
    $job = Start-Job -ScriptBlock {
        param($s)
        Start-Sleep -Seconds $s
        return "done"
    } -ArgumentList $Seconds
    return $job
}

function Wait-Work {
    param($Job)
    $result = Receive-Job -Job $Job -Wait -AutoRemoveJob
    return $result
}
