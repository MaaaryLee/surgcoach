# Type C sample for review: C1, C2, C3, C6, C7 across six trials chosen to span
# the GRS range, so the output can be judged on whether it discriminates rather
# than just on whether it is grounded.
#
# C4 and C5 are absent on purpose. JIGSAWS has no bimanual-coordination label and
# no targeting/accuracy label, so the runner refuses them -- asking anyway returns
# "visual evidence needed", which is the correct answer to an impossible question,
# not a failure of the pipeline.
#
#   Suturing_C001        I  GRS 26  (4 4 4 5 4 5)  strongest
#   Knot_Tying_C002      I  GRS 22  (4 3 3 4 4 4)
#   Needle_Passing_B004  N  GRS 19  (3 3 3 4 3 3)  middling
#   Suturing_B004        N  GRS 10  (1 1 2 3 2 1)
#   Knot_Tying_B002      N  GRS  9  (1 1 2 2 1 2)
#   Needle_Passing_C003  I  GRS  7  (1 1 1 2 1 1)  weakest

$ErrorActionPreference = "Stop"
$repo = "c:\Users\Eric\Project\surgcoach"
$outRoot = "$repo\outputs\type_c_sample_7-28-2026"

$runs = @(
    @{ Task = "Suturing";       Trials = "Suturing_C001,Suturing_B004" },
    @{ Task = "Knot_Tying";     Trials = "Knot_Tying_C002,Knot_Tying_B002" },
    @{ Task = "Needle_Passing"; Trials = "Needle_Passing_B004,Needle_Passing_C003" }
)

function Resolve-DatasetRoot([string]$task) {
    $hit = Get-ChildItem "$repo\outputs\datasets\JIGSAWS\$task" -Recurse `
        -Filter "meta_file_$task.txt" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $hit) { throw "no meta_file_$task.txt under outputs\datasets\JIGSAWS\$task" }
    return $hit.Directory.FullName
}

New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
$roots = @{}
foreach ($run in $runs) { $roots[$run.Task] = Resolve-DatasetRoot $run.Task }

foreach ($run in $runs) {
    $task = $run.Task
    Write-Output "=== $task ==="
    python "$repo\scripts\run_annotation_qa_jigsaws.py" `
        --dataset-root $roots[$task] `
        --system-prompt "$repo\Prompts_And_Pipeline\system-prompt-A-D.md" `
        --task $task `
        --trial-ids $run.Trials `
        --granularity video `
        --templates "C1,C2,C3,C6,C7" `
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

# -Encoding UTF8 on the read and UTF8Encoding($false) on the write: without the
# first, PowerShell 5.1 decodes the runner's UTF-8 as the ANSI codepage and
# corrupts every em-dash; without the second it writes a BOM that json.loads
# rejects.
$combined = "$outRoot\qa_records.jsonl"
$lines = foreach ($run in $runs) { Get-Content "$outRoot\$($run.Task)\qa_records.jsonl" -Encoding UTF8 }
[System.IO.File]::WriteAllLines($combined, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "=== combined: $((Get-Content $combined | Measure-Object -Line).Lines) records ==="
