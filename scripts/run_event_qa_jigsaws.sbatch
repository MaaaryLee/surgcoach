#!/usr/bin/env bash
#SBATCH --job-name=event-qa-jigsaws
#SBATCH --partition=a6000
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=8
#SBATCH --mem=160G
#SBATCH --time=24:00:00
#SBATCH --output=event-qa-jigsaws-%j.out
#SBATCH --error=event-qa-jigsaws-%j.err

# Event-grounded QA over all of JIGSAWS, at full precision.
#
# Different questions from the C/D job, not a rerun of it. C/D ask one broad
# question per trial against six trial-level scores, which is what review found
# generic: nothing in the input distinguishes one moment from another, so nothing
# in the output can. These questions each state a measured fact about one span --
# how long a gesture took against the strongest recorded attempts at it, how far
# an instrument travelled, how many times a step was repeated -- so the question
# differs per trial and the answer is checkable without watching the video.
#
# Up to 5 questions per trial (2 duration outliers, 1 excess travel, 1 repetition,
# 1 economy, 1 clean execution), so roughly 350-500 records over 103 trials rather
# than the fixed 515 the C/D job produces. Fewer than 5 for a trial is a result,
# not a bug: a trial with no span past threshold has nothing worth localizing.
#
# Run scripts/preflight_event_qa.sbatch first. It costs two minutes, needs no GPU,
# and checks everything this job assumes -- work dir reachable from a compute
# node, kinematics present, headers found, numpy/torch importable, and the
# question builder able to run on cluster data. This job would otherwise load a
# 69 GB model before discovering any of it.
#
#   sbatch scripts/preflight_event_qa.sbatch          # read the .out first
#   sbatch scripts/run_event_qa_jigsaws.sbatch
#   WORK_DIR=/home/$USER/annotation_qa sbatch scripts/run_event_qa_jigsaws.sbatch
#
# Pass variables as an environment prefix, NOT through --export. sbatch's --export
# list is comma separated, so --export=ALL,TRIALS="a,b,c" silently sets TRIALS=a and
# treats b and c as variable names. That cost a job: TRIALS held 10 trials, one
# arrived, and the log's "explicit list (1 trials)" was the only sign. This works:
#   TRIALS="a,b,c" sbatch scripts/run_event_qa_jigsaws.sbatch
# and combines fine with sbatch's own flags:
#   TRIALS="a,b,c" sbatch --exclude=somenode scripts/run_event_qa_jigsaws.sbatch
#
# Resumable: --resume skips every qa_id already recorded valid, and records flush
# one at a time, so a preemption costs at most one question.

set -euo pipefail

# Slurm runs a COPY of this script from its own spool directory, so ${BASH_SOURCE[0]}
# points at /var/lib/slurm/slurmd/jobNNNNN/ and nothing else from the repo is beside
# it. Resolved once here, for every later source, because getting this wrong fails
# late: job 28435 loaded the env file (which had its own fallback) and then died on
# "stratified_trials.sh: No such file or directory" after printing a page of
# apparently healthy setup.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$HERE/cluster_env_event_qa.sh" ]; then
  HERE="${SLURM_SUBMIT_DIR:-$PWD}/scripts"
fi
if [ ! -f "$HERE/cluster_env_event_qa.sh" ]; then
  echo "ERROR: cannot find the repo's scripts/ directory. Tried the script's own" >&2
  echo "       location and \${SLURM_SUBMIT_DIR:-\$PWD}/scripts = $HERE" >&2
  echo "       Submit from the repo root, or set WORK_DIR." >&2
  exit 1
fi
echo "SCRIPTS             $HERE"
# shellcheck source=cluster_env_event_qa.sh
. "$HERE/cluster_env_event_qa.sh"
printf '%s' "$CLUSTER_NOTES"

MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-9000}"
# Sampling rather than greedy: records from one trial share an underlying
# comparison, and greedy decoding was observed converging on near-identical
# phrasing across them. 0.7 trades exact reproducibility for lexical variety,
# which is the right trade when building a dataset.
TEMPERATURE="${TEMPERATURE:-0.7}"
MAX_RETRIES="${MAX_RETRIES:-3}"
OUT_ROOT="${OUT_ROOT:-$WORK_DIR/outputs/event_qa_${SLURM_JOB_ID:-manual}}"
TASKS_TO_RUN="${TASKS_TO_RUN:-Suturing Knot_Tying Needle_Passing}"

# core (default): the 7 trials marked core in scripts/stratified_trials.txt, 31
#   records. Keeps all three tasks, GRS 6-30, all three skill labels and both
#   skill/GRS decoupling cases -- see that file for why those 7.
# stratified: all 15 trials, 60 records. The C and D batches use the same 15, so
#   each of those videos gets all three record sets and a generic answer can be
#   put beside a localized one for the same trial.
# all: every trial with both a meta row and a transcription, 392 records.
SUBSET="${SUBSET:-core}"

# USE_ERROR_LABELS=1 builds questions from the UVA-DSA recorded errors instead of
# from motion proxies. The generator locates them itself -- beside JIGSAWS on the
# shared mount here, under outputs/ locally -- so a path is only needed for an
# unusual location, via ERROR_LABELS.
#
#   USE_ERROR_LABELS=1 TRIALS=... sbatch scripts/run_event_qa_jigsaws.sbatch
#
# The labels are not redistributed with this repo: the source states no licence, so
# they are cloned separately and live outside the tree.
#   cd /mnt/sun/shared/datasets/surgical_skill \
#     && git clone --depth 1 \
#          https://github.com/UVA-DSA/ExecProc_Error_Analysis.git ExecProc_Error
#
# Proxy questions remain the default because they are the only ones covering
# Knot_Tying, which has no error labels at all.
# How the question names the moment it asks about. Only consulted with error labels.
#
#   timestamp  (default) "At 0:05.5-0:19.5 the trainee was ..."
#   ordinal    the clock removed, the moment named by which occurrence it was
#   none       names no moment at all: "The trainee was X multiple times in this
#              recording. How did those go?" -- the model has to find which
#              occurrence went wrong, which is the half of the task a timestamp
#              deletes. This is the only mode that produces negative examples,
#              because a gesture whose occurrences are all clean is a valid
#              question whose answer is "they went fine".
#
#   USE_ERROR_LABELS=1 ANCHOR=none TRIALS=... sbatch scripts/run_event_qa_jigsaws.sbatch
ANCHOR="${ANCHOR:-timestamp}"
case "$ANCHOR" in
  timestamp|ordinal|none) ;;
  *) echo "ERROR: ANCHOR must be timestamp, ordinal or none; got '$ANCHOR'" >&2; exit 1 ;;
esac
echo "ANCHOR              $ANCHOR"

LABEL_ARGS=()
if [ -n "${ERROR_LABELS:-}" ]; then
  if [ ! -d "$ERROR_LABELS" ]; then
    echo "ERROR: ERROR_LABELS=$ERROR_LABELS is not a directory" >&2
    exit 1
  fi
  LABEL_ARGS=(--error-labels "$ERROR_LABELS")
  echo "ERROR_LABELS        $ERROR_LABELS"
elif [ -n "${USE_ERROR_LABELS:-}" ]; then
  LABEL_ARGS=(--error-labels)
  echo "ERROR_LABELS        auto-detected by the generator"
fi
echo "OUT_ROOT            $OUT_ROOT"
# An explicit TRIALS list supersedes SUBSET entirely, so say which one is in force
# rather than printing a SUBSET that will not be consulted -- job 28435's log said
# "SUBSET core" while TRIALS was set, which is misleading either way.
if [ -n "${TRIALS:-}" ]; then
  echo "TRIALS              explicit list ($(echo "$TRIALS" | tr ',' '\n' | grep -c .) trials)"
  # Narrow TASKS_TO_RUN to the tasks those trials actually belong to, rather than
  # entering the loop for all three and skipping the empty ones. This matters for
  # error-grounded runs specifically: ExecProc_Error_Analysis annotates Suturing and
  # Needle Passing only, so Knot_Tying can never appear in an error-label trial list.
  # Iterating over it anyway meant validating its kinematics for nothing and relying
  # on a skip path -- and when that path had a `grep` that exits 1 on no match, under
  # `set -euo pipefail`, the job died there silently. Not generating a task is more
  # honest than generating it and discarding the result.
  derived=""
  for known in Suturing Knot_Tying Needle_Passing; do
    if echo "$TRIALS" | tr ',' '\n' | grep -q "^${known}_"; then
      derived="$derived $known"
    fi
  done
  if [ -z "$derived" ]; then
    echo "ERROR: no trial in \$TRIALS matches a known task name." >&2
    echo "       Expected ids like Suturing_B002 or Needle_Passing_C003; got: $TRIALS" >&2
    exit 1
  fi
  TASKS_TO_RUN="${derived# }"
  echo "TASKS_TO_RUN        $TASKS_TO_RUN  (derived from \$TRIALS)"
elif [ "$SUBSET" != "all" ]; then
  echo "SUBSET              $SUBSET"
  # Only needed for the SUBSET path; sourcing it otherwise is a file dependency for
  # no reason.
  . "$HERE/stratified_trials.sh"
else
  echo "SUBSET              all"
fi

if [ ! -d "$QWEN36_PACKAGE_DIR" ]; then
  echo "ERROR: no torch/transformers at $QWEN36_PACKAGE_DIR." >&2
  exit 1
fi
if [ -z "${HEADER_ROOT:-}" ]; then
  echo "ERROR: no Python.h found. Triton JIT-compiles a helper for Qwen3.6's MoE" >&2
  echo "       kernels and needs it. Extract a copy without root:" >&2
  echo "         mkdir -p \$HOME/pydev && cd \$HOME/pydev \\" >&2
  echo "           && apt download libpython3.10-dev && dpkg -x libpython3.10-dev*.deb ." >&2
  exit 1
fi
if [ -z "${SYSTEM_PROMPT:-}" ] || [ ! -f "$SYSTEM_PROMPT" ]; then
  echo "ERROR: system-prompt-events.md not found under $WORK_DIR." >&2
  exit 1
fi

if ! mkdir -p "$OUT_ROOT" "$TMPDIR" "$HF_HOME" 2>/dev/null; then
  echo "ERROR: cannot create OUT_ROOT=$OUT_ROOT TMPDIR=$TMPDIR HF_HOME=$HF_HOME" >&2
  exit 1
fi
cd "$WORK_DIR"

# Fail before the model load if the dataset lacks kinematics. The C/D pipeline
# reads meta_file + transcriptions only, so a root that worked for those jobs can
# still be missing the third source this one needs -- and a missing kinematics
# directory yields zero events silently rather than erroring.
for task in $TASKS_TO_RUN; do
  meta=$(find "$JIGSAWS_ROOT/$task" -name "meta_file_${task}.txt" -print -quit 2>/dev/null || true)
  if [ -z "$meta" ]; then
    echo "ERROR: no meta_file_${task}.txt under $JIGSAWS_ROOT/$task" >&2
    exit 1
  fi
  root=$(dirname "$meta")
  n_kin=$(find "$root/kinematics" -name '*.txt' 2>/dev/null | grep -c . || true)
  if [ "$n_kin" -eq 0 ]; then
    echo "ERROR: no kinematics under $root/kinematics; the event detectors need it." >&2
    exit 1
  fi
  echo "$task: $root ($n_kin kinematics files)"
done

# Neither cluster isolates GPUs between jobs: another user's process can occupy
# one Slurm handed us, which makes accelerate offload layers to CPU and then OOM
# mid-run, hours in. Pin to GPUs that are actually free, waiting rather than
# dying if too few are.
NUM_FREE=0
FREE_GPUS=""
for attempt in $(seq 1 "${GPU_WAIT_TRIES:-60}"); do
  FREE_GPUS=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits | awk -F', ' '$2 < 2000 {print $1}' | paste -sd, -)
  NUM_FREE=$(echo "$FREE_GPUS" | tr ',' '\n' | grep -c . || true)
  if [ "$NUM_FREE" -ge "${MIN_FREE_GPUS:-3}" ]; then break; fi
  echo "poll $attempt/${GPU_WAIT_TRIES:-60}: only $NUM_FREE free GPUs (<${MIN_FREE_GPUS:-3}); sleeping 300s"
  sleep 300
done
if [ "$NUM_FREE" -lt "${MIN_FREE_GPUS:-3}" ]; then
  echo "ERROR: still only $NUM_FREE free GPUs after waiting; giving up" >&2
  exit 1
fi
# Cap how many this job claims. Defaults to what Slurm actually allocated rather
# than a fixed number, because the two silently diverged: with a hardcoded 8, a
# job requesting --gres=gpu:4 that found 8 free GPUs pinned all 8. That runs, but
# Slurm still believes 4 are available and hands them to someone else, who then
# hits the very "another user's process took my GPU" failure this poll loop
# exists to avoid. 70 GB of weights across 4 A6000s is already comfortable, so
# the extra GPUs bought nothing.
FREE_GPUS=$(echo "$FREE_GPUS" | tr ',' '\n' | head -n "${MAX_GPUS:-${SLURM_GPUS_ON_NODE:-4}}" | paste -sd, -)
echo "using GPUs: [$FREE_GPUS]"
export CUDA_VISIBLE_DEVICES="$FREE_GPUS"

# One invocation per task. The comparison group is built per task from the other
# trials in that task, so tasks are independent and a failure in one leaves the
# others' output intact.
for task in $TASKS_TO_RUN; do
  echo "=== $task ==="
  # --trials names them explicitly; --task takes every trial with a transcription.
  # The generator infers the task from each trial id, so --task is not also needed.
  if [ -n "${TRIALS:-}" ]; then
    # An explicit list, for error-grounded runs. Those select trials by which errors
    # they contain rather than by the stratified sample, and only Suturing and
    # Needle Passing have error labels at all, so neither SUBSET value describes the
    # set. Filtered per task so the per-task loop still works.
    # `|| true` matters: grep exits 1 when a task has no trials in the list, and with
    # `set -euo pipefail` that status propagates through the assignment and kills the
    # job. Job 28437 finished Suturing's 24 records, hit Knot_Tying -- which has no
    # error labels and so no trials here -- and died before Needle_Passing and before
    # the combine step, having printed nothing that looked like an error.
    selected=$(echo "$TRIALS" | tr ',' '\n' | grep "^${task}_" | paste -sd, - || true)
    if [ -z "$selected" ]; then
      echo "  no trials in \$TRIALS belong to $task, skipping"
      continue
    fi
    echo "  trials: $selected"
    SELECT_ARGS=(--trials "$selected")
  elif [ "$SUBSET" != "all" ]; then
    selected=$(trials_for_task "$task" "$SUBSET")
    if [ -z "$selected" ]; then
      echo "  no $SUBSET trials listed for $task, skipping"
      continue
    fi
    echo "  trials: $selected"
    SELECT_ARGS=(--trials "$selected")
  else
    SELECT_ARGS=(--task "$task")
  fi
  python3 scripts/generate_event_qa.py \
    --jigsaws-root "$JIGSAWS_ROOT" \
    --system-prompt "$SYSTEM_PROMPT" \
    "${SELECT_ARGS[@]}" \
    ${LABEL_ARGS[@]+"${LABEL_ARGS[@]}"} \
    --anchor "$ANCHOR" \
    --backend transformers \
    --model-id "$MODEL_ID" \
    --device-map auto \
    --max-new-tokens "$MAX_NEW_TOKENS" \
    --temperature "$TEMPERATURE" \
    --max-retries "$MAX_RETRIES" \
    --resume \
    --output-dir "$OUT_ROOT/$task"
done

# Combine, in python so the encoding is explicit and no BOM is written -- a
# leading BOM makes json.loads reject the first line outright.
python3 - "$OUT_ROOT" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
parts = sorted(p for p in root.rglob("qa_records.jsonl") if p.parent != root)
lines = []
for p in parts:
    lines += [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
(root / "qa_records.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"combined {len(lines)} records from {len(parts)} task files")
PY

echo "done. check with: python3 scripts/check_event_qa.py $OUT_ROOT"
