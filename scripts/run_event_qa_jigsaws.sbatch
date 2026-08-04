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
# Different questions from the C/D job, not just a rerun of it. C/D ask one broad
# question per trial against six trial-level scores, which is what review found
# generic: nothing in the input distinguishes one moment from another, so nothing
# in the output can. These questions each state a measured fact about one span --
# how long a gesture took against the strongest recorded attempts at it, how far
# an instrument travelled, how many times a step was repeated -- so the question
# differs per trial and the answer is checkable without watching the video.
#
# Up to 5 questions per trial (2 duration outliers, 1 excess travel, 1 repetition,
# 1 economy, 1 clean execution), so ~350-500 records over 103 trials rather than
# the fixed 515 the C/D job produces. Fewer than 5 for a trial is a result, not a
# bug: a trial with no span past the threshold has nothing worth localizing.
#
# Run scripts/preflight_event_qa.sbatch first. It costs two minutes, needs no GPU,
# and checks the four things this job assumes -- work dir reachable from a compute
# node, kinematics present, numpy/torch importable, and the question builder able
# to run on cluster data. This job loads a 69 GB model before it would discover
# any of them.
#
#   sbatch scripts/preflight_event_qa.sbatch      # then read the .out
#   sbatch scripts/run_event_qa_jigsaws.sbatch
#
# Resumable: --resume skips every qa_id already recorded valid, and records are
# flushed one at a time, so a preemption costs at most one question.

set -euo pipefail

WORK_DIR="${WORK_DIR:-$PWD}"
JIGSAWS_ROOT="${JIGSAWS_ROOT:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$WORK_DIR/Prompts_And_Pipeline/system-prompt-events.md}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-9000}"
TEMPERATURE="${TEMPERATURE:-0.7}"
MAX_RETRIES="${MAX_RETRIES:-3}"
OUT_ROOT="${OUT_ROOT:-$WORK_DIR/outputs/event_qa_${SLURM_JOB_ID:-manual}}"
TASKS_TO_RUN="${TASKS_TO_RUN:-Suturing Knot_Tying Needle_Passing}"

ENDPOINT="${ENDPOINT:-$(dirname "$(cd "$WORK_DIR" && pwd)")}"
echo "ENDPOINT=$ENDPOINT"
echo "WORK_DIR=$WORK_DIR"
echo "OUT_ROOT=$OUT_ROOT"

QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
if [ ! -d "$QWEN36_PACKAGE_DIR" ]; then
  echo "ERROR: no torch/transformers at $QWEN36_PACKAGE_DIR." >&2
  exit 1
fi
export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Triton (inside Qwen3.6's MoE code) JIT-compiles a C helper and needs Python.h,
# which this cluster has no system-wide copy of. Extract one without root:
#   cd $ENDPOINT && mkdir -p pydev && cd pydev \
#     && apt download libpython3.10-dev && dpkg -x libpython3.10-dev*.deb .
PYDEV_ROOT="${PYDEV_ROOT:-$ENDPOINT/pydev/usr/include}"
if [ ! -d "$PYDEV_ROOT/python3.10" ]; then
  echo "ERROR: no Python.h headers at $PYDEV_ROOT/python3.10 (see comment above)." >&2
  exit 1
fi
export CPATH="$PYDEV_ROOT/python3.10:$PYDEV_ROOT/x86_64-linux-gnu/python3.10:$PYDEV_ROOT${CPATH:+:$CPATH}"

export HF_HOME="${HF_HOME:-$ENDPOINT/.cache/huggingface}"
export TMPDIR="${TMPDIR:-$ENDPOINT/.tmp/event_qa}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export PYTHONFAULTHANDLER=1

if ! mkdir -p "$OUT_ROOT" "$TMPDIR" "$HF_HOME" 2>/dev/null; then
  echo "ERROR: cannot create OUT_ROOT=$OUT_ROOT TMPDIR=$TMPDIR HF_HOME=$HF_HOME" >&2
  echo "       Usually means \$ENDPOINT is wrong; it resolved to $ENDPOINT." >&2
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

# This cluster does not isolate GPUs: another user's process can occupy one Slurm
# handed us, which makes accelerate offload layers to CPU and then OOM mid-run.
# Pin to GPUs that are actually free, waiting rather than dying.
NUM_FREE=0
FREE_GPUS=""
for attempt in $(seq 1 "${GPU_WAIT_TRIES:-60}"); do
  FREE_GPUS=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits | awk -F', ' '$2 < 2000 {print $1}' | paste -sd, -)
  NUM_FREE=$(echo "$FREE_GPUS" | tr ',' '\n' | grep -c . || true)
  if [ "$NUM_FREE" -ge "${MIN_FREE_GPUS:-3}" ]; then break; fi
  echo "poll $attempt/${GPU_WAIT_TRIES:-60}: only $NUM_FREE free GPUs (<${MIN_FREE_GPUS:-3}); sleeping 300s"
  sleep 300
done
echo "free GPUs: [$FREE_GPUS] count=$NUM_FREE"
if [ "$NUM_FREE" -lt "${MIN_FREE_GPUS:-3}" ]; then
  echo "ERROR: still only $NUM_FREE free GPUs after waiting; giving up" >&2
  exit 1
fi
export CUDA_VISIBLE_DEVICES="$FREE_GPUS"

# One invocation per task. The comparison group is built per task from the other
# trials in that task, so tasks are independent and a failure in one leaves the
# others' output intact.
for task in $TASKS_TO_RUN; do
  echo "=== $task ==="
  python3 scripts/generate_event_qa.py \
    --jigsaws-root "$JIGSAWS_ROOT" \
    --system-prompt "$SYSTEM_PROMPT" \
    --task "$task" \
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
