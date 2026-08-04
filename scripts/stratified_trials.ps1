# Reads scripts/stratified_trials.txt, which holds the list and the reasoning
# behind it. This file only reshapes it into the $StratifiedTrials structure the
# C and D runners already expect, so the list itself lives in exactly one place
# now that the cluster sbatch scripts need it too.

$listPath = Join-Path $PSScriptRoot "stratified_trials.txt"
if (-not (Test-Path $listPath)) {
    throw "no trial list at $listPath"
}

# -Encoding utf8 explicitly: PowerShell 5.1's Get-Content defaults to the system
# ANSI codepage, which has silently corrupted files in this repo before.
$rows = Get-Content $listPath -Encoding utf8 |
    Where-Object { $_ -notmatch '^\s*#' -and $_ -match '\S' } |
    ForEach-Object { , ($_ -split '\s+' | Where-Object { $_ }) }

# Preserve the file's task order rather than sorting, so run logs and the file
# read the same way.
$taskOrder = @()
foreach ($row in $rows) {
    if ($taskOrder -notcontains $row[0]) { $taskOrder += $row[0] }
}

$StratifiedTrials = @()
foreach ($task in $taskOrder) {
    $trials = @($rows | Where-Object { $_[0] -eq $task } | ForEach-Object { $_[1] })
    $StratifiedTrials += @{ Task = $task; Trials = ($trials -join ",") }
}
