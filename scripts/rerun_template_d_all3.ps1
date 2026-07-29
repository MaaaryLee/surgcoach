# Regenerate the full Template D batch (75 records) under the post-fix prompt.
#
# Replaces "outputs after skill-level gloss and anatomy rule - 7-26-2026", which
# has two records that assert behaviour the annotations cannot support
# (D2/D3 Knot_Tying_B001). Hard rule 1 now covers consequences and severity, so
# the whole batch is regenerated rather than patched -- a partial rerun would
# leave the batch split across two prompt versions.
#
# Same trials, templates and sampling settings as that batch, so the only
# variable is the prompt. Tasks run separately: one invocation takes a single
# --task, and the opener-threading mechanism threads within a run.

$ErrorActionPreference = "Stop"
$repo = "c:\Users\Eric\Project\surgcoach"
# _v3. v1 ran under a prompt that had grown 19% from repeated appends, leaving too
# little context for the model's reasoning and truncating 11 of 75 records. v2 was
# stopped early: the prompt was consolidated back under budget, but its subscore
# glosses were paraphrases, and reading the real OSATS anchors showed one of them
# ("time and motion measures fluency") was simply wrong. v3 carries the verbatim
# 1/3/5 anchor text instead. Neither earlier output is committed -- see the repo
# results policy on failed runs.
$outRoot = "$repo\outputs\template_d_rerun_v4_7-28-2026"

$runs = @(
    @{ Task = "Suturing";       Trials = "Suturing_B001,Suturing_B002,Suturing_B003,Suturing_B004,Suturing_B005" },
    @{ Task = "Knot_Tying";     Trials = "Knot_Tying_B001,Knot_Tying_B002,Knot_Tying_B003,Knot_Tying_B004,Knot_Tying_C001" },
    @{ Task = "Needle_Passing"; Trials = "Needle_Passing_B001,Needle_Passing_B002,Needle_Passing_B003,Needle_Passing_B004,Needle_Passing_C001" }
)

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
foreach ($run in $runs) {
    $task = $run.Task
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
#            turned every em-dash into "â€”" in the combined file while the
#            per-task files stayed clean.
#   write -- -Encoding utf8 emits a BOM, which json.loads rejects outright
#            ("Unexpected UTF-8 BOM"), so UTF8Encoding($false) is required.
$combined = "$outRoot\qa_records.jsonl"
$lines = foreach ($run in $runs) { Get-Content "$outRoot\$($run.Task)\qa_records.jsonl" -Encoding UTF8 }
[System.IO.File]::WriteAllLines($combined, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "=== combined: $((Get-Content $combined | Measure-Object -Line).Lines) records ==="
