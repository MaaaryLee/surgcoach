# Template C across all three JIGSAWS tasks: C1, C2, C3, C6, C7 over the same 15
# trials as the Template D batch, so every video has both a C and a D record set
# rather than the two describing different videos.
#
# C4 and C5 are absent by design, not oversight. Bimanual dexterity and depth
# perception are GEARS/GOALS domains; JIGSAWS scores a modified OSATS, which has
# neither, so the runner refuses them. Asking anyway returns "visual evidence
# needed" -- the correct answer to an impossible question, and the reason two
# earlier attempts at Type C looked like dataset failures when they were wording
# and template-choice failures.
#
# Mirrors the D driver: dataset roots resolved up front, tasks with a complete
# record set skipped so a late crash keeps finished work, and both sides of the
# concatenation given an explicit encoding.

$ErrorActionPreference = "Stop"
$repo = "c:\Users\Eric\Project\surgcoach"
$outRoot = "$repo\outputs\template_c_stratified_7-29-2026"

. "$repo\scripts\stratified_trials.ps1"
$runs = $StratifiedTrials
$templates = "C1,C2,C3,C6,C7"

function Resolve-DatasetRoot([string]$task) {
    $hit = Get-ChildItem "$repo\outputs\datasets\JIGSAWS\$task" -Recurse `
        -Filter "meta_file_$task.txt" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $hit) { throw "no meta_file_$task.txt under outputs\datasets\JIGSAWS\$task" }
    return $hit.Directory.FullName
}

function Get-RecordCount([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    return (Get-Content $path -Encoding UTF8 | Where-Object { $_.Trim() } | Measure-Object).Count
}

New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
$roots = @{}
foreach ($run in $runs) { $roots[$run.Task] = Resolve-DatasetRoot $run.Task }
$roots.GetEnumerator() | ForEach-Object { Write-Output "dataset root: $($_.Key) -> $($_.Value)" }

foreach ($run in $runs) {
    $task = $run.Task
    $expected = ($run.Trials -split ',').Count * ($templates -split ',').Count
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
        --templates $templates `
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

$combined = "$outRoot\qa_records.jsonl"
$lines = foreach ($run in $runs) { Get-Content "$outRoot\$($run.Task)\qa_records.jsonl" -Encoding UTF8 }
[System.IO.File]::WriteAllLines($combined, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "=== combined: $((Get-Content $combined | Measure-Object -Line).Lines) records ==="
