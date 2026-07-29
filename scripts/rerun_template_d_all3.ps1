# Template D across all three JIGSAWS tasks: D1-D5 over the 15 trials in
# stratified_trials.ps1, one question per whole video.
#
# The trial list is shared with the Template C driver so every video ends up
# with both a C and a D record set. It spans each task's GRS range rather than
# taking the first files alphabetically, which earlier runs did: that gave 13 of
# 15 trials scoring 17 or below out of 30 and no expert trial at all, and two
# templates could not function on it -- C7 used one of its five supervision
# levels for all 15 records, and D5 was asked to name a strongest skill in
# trials that had no strength to name.
#
$ErrorActionPreference = "Stop"
$repo = "c:\Users\Eric\Project\surgcoach"
$outRoot = "$repo\outputs\template_d_stratified_7-29-2026"

. "$repo\scripts\stratified_trials.ps1"
$runs = $StratifiedTrials

New-Item -ItemType Directory -Force -Path $outRoot | Out-Null

# The local dataset copy is not laid out uniformly: Suturing sits at
# JIGSAWS\Suturing, but Knot_Tying and Needle_Passing are nested one level
# deeper (JIGSAWS\Knot_Tying\Knot_Tying). Locate meta_file_<task>.txt instead of
# assuming either shape, so a layout difference fails here rather than an hour
# into the run.
function Resolve-DatasetRoot([string]$task) {
    $hit = Get-ChildItem "$repo\outputs\datasets\JIGSAWS\$task" -Recurse `
        -Filter "meta_file_$task.txt" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $hit) { throw "no meta_file_$task.txt under outputs\datasets\JIGSAWS\$task" }
    return $hit.Directory.FullName
}

# Fail fast on all three before generating anything.
$roots = @{}
foreach ($run in $runs) { $roots[$run.Task] = Resolve-DatasetRoot $run.Task }
$roots.GetEnumerator() | ForEach-Object { Write-Output "dataset root: $($_.Key) -> $($_.Value)" }

# --max-retries is 3 rather than 2: v3 lost exactly one record to a JSON failure
# that had exhausted its retries. Retries fire only on malformed output, never on
# content, so raising this cannot select for answers that please the checker.
# (Keep comments out of the backtick-continued argument list below -- a comment
# breaks the line continuation and the script fails to parse.)
#
# Tasks that already hold a complete record set are skipped, so a crash in the
# third task does not discard the first two. An Ollama HTTP 500 killed one run
# 39 records in after Suturing had finished, and without this the whole two hours
# would have been repeated. A task counts as complete only at the full expected
# record count; a partial file is regenerated rather than topped up, since the
# runner opens its output with "w".
function Get-RecordCount([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    return (Get-Content $path -Encoding UTF8 | Where-Object { $_.Trim() } | Measure-Object).Count
}

foreach ($run in $runs) {
    $task = $run.Task
    $expected = ($run.Trials -split ',').Count * 5
    $existing = Get-RecordCount "$outRoot\$task\qa_records.jsonl"
    if ($existing -eq $expected) {
        Write-Output "=== $task === already complete ($existing/$expected records), skipping"
        continue
    }
    if ($existing -gt 0) {
        Write-Output "=== $task === partial ($existing/$expected records), regenerating"
    }
    Write-Output "=== $task ==="
    python "$repo\scripts\run_annotation_qa_jigsaws.py" `
        --dataset-root $roots[$task] `
        --system-prompt "$repo\Prompts_And_Pipeline\system-prompt-A-D.md" `
        --task $task `
        --trial-ids $run.Trials `
        --granularity video `
        --templates "D1,D2,D3,D4,D5" `
        --inference-engine ollama `
        --ollama-model "qwen3.6-35b-a3b-iq4xs" `
        --model-id "qwen3.6-35b-a3b-iq4xs" `
        --ollama-num-ctx 12288 `
        --max-new-tokens 9000 `
        --temperature 0.7 `
        --max-retries 3 `
        --output-dir "$outRoot\$task"
    if ($LASTEXITCODE -ne 0) { throw "$task run failed with exit code $LASTEXITCODE" }
}

# Concatenate into one batch file, task order matching the run order above.
#
# Both halves of this need an explicit encoding, and each was wrong once:
#   read  -- Get-Content without -Encoding uses the system ANSI codepage in
#            PowerShell 5.1, so it decoded the runner's UTF-8 as cp1252 and
#            mangled every em-dash in the combined file while the per-task
#            files stayed clean. Editing this file with Get-Content -Raw and
#            no -Encoding re-corrupted this very comment once: the bug
#            demonstrating itself.
#   write -- -Encoding utf8 emits a BOM, which json.loads rejects outright
#            ("Unexpected UTF-8 BOM"), so UTF8Encoding($false) is required.
$combined = "$outRoot\qa_records.jsonl"
$lines = foreach ($run in $runs) { Get-Content "$outRoot\$($run.Task)\qa_records.jsonl" -Encoding UTF8 }
[System.IO.File]::WriteAllLines($combined, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "=== combined: $((Get-Content $combined | Measure-Object -Line).Lines) records ==="

