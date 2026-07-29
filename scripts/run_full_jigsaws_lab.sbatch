#!/usr/bin/env bash
#SBATCH --job-name=anno-qa-full-jigsaws
#SBATCH --partition=a6000
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=8
#SBATCH --mem=160G
#SBATCH --time=24:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

# Templates C and D over ALL of JIGSAWS: every trial that has both a meta_file
# entry and a transcription, one question per whole video.
#
#   Suturing        39 trials   GRS  8-30
#   Knot_Tying      36 trials   GRS  6-22
#   Needle_Passing  28 trials   GRS  7-24
#   103 trials x 5 D templates + 103 x 5 C templates = 1030 records
#
# Why the cluster rather than the local 4070:
#   - Full precision instead of 4-bit. Every batch so far has been iq4xs, and
#     whether quantisation costs anything in coaching quality has never been
#     tested. This run answers that, so its output is NOT comparable with the
#     local batches and must not be mixed with them.
#   - No CPU offload. Locally an 18 GB model against 12 GB of VRAM ran ~43% on
#     CPU and capped the context at 12288 tokens, which is what caused every
#     truncation failure. Reasoning is ~89% of what this model generates, so that
#     cap was the binding constraint, not the prompt.
#   - Volume. "2 content issues per 75 records" is too small a sample to tell 2.7%
#     from 1% or 5%. 1030 records gives a rate worth quoting.
#
# Submit from $ENDPOINT/surgcoach -- on megatron that is /mnt/meg/$USER/surgcoach,
# not $HOME. SLURM writes the log files above into the submit directory and $HOME
# hits permission errors on this cluster. Requires $ENDPOINT set in ~/.bashrc.
#
# Resumable: a task/family whose output already holds the full expected record
# count is skipped, so a timeout or preemption does not discard finished work.
# Re-submit the same script to continue.

set -euo pipefail

WORK_DIR="${WORK_DIR:-$PWD}"
JIGSAWS_ROOT="${JIGSAWS_ROOT:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$WORK_DIR/Prompts_And_Pipeline/system-prompt-A-D.md}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
# 9000 to match the local runs. 6000 truncates: reasoning alone runs to ~18k
# characters, roughly 4500 tokens, before any JSON is emitted.
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-9000}"
# Sampling rather than greedy. Records from one trial share the same GRS scores,
# and greedy decoding was observed converging on near-identical phrasing across
# them; 0.7 trades exact reproducibility for lexical variety, which is the right
# trade when building a dataset.
TEMPERATURE="${TEMPERATURE:-0.7}"
MAX_RETRIES="${MAX_RETRIES:-3}"
OUT_ROOT="${OUT_ROOT:-$WORK_DIR/outputs/full_jigsaws_${SLURM_JOB_ID:-manual}}"

D_TEMPLATES="${D_TEMPLATES:-D1,D2,D3,D4,D5}"
# C4 and C5 are absent deliberately: bimanual dexterity and depth perception are
# GEARS/GOALS domains and JIGSAWS scores a modified OSATS, which has neither, so
# the runner refuses them. Asking anyway returns "visual evidence needed".
C_TEMPLATES="${C_TEMPLATES:-C1,C2,C3,C6,C7}"

QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
PYDEV_ROOT="${PYDEV_ROOT:-$ENDPOINT/pydev/usr/include}"
export CPATH="$PYDEV_ROOT/python3.10:$PYDEV_ROOT/x86_64-linux-gnu/python3.10:$PYDEV_ROOT${CPATH:+:$CPATH}"
export HF_HOME="${HF_HOME:-$ENDPOINT/.cache/huggingface}"
export TMPDIR="${TMPDIR:-$ENDPOINT/.tmp/annotation_qa}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export PYTHONFAULTHANDLER=1

mkdir -p "$OUT_ROOT" "$TMPDIR" "$HF_HOME"
cd "$WORK_DIR"

# The dataset layout is not uniform: Suturing may sit at JIGSAWS/Suturing while
# the other two are nested a level deeper. Locate meta_file_<task>.txt rather
# than assuming either shape, and fail before loading a 69 GB model if a task is
# missing.
resolve_root() {
  local task="$1" hit
  hit=$(find "$JIGSAWS_ROOT/$task" -name "meta_file_${task}.txt" -print -quit 2>/dev/null || true)
  if [ -z "$hit" ]; then
    echo "ERROR: no meta_file_${task}.txt under $JIGSAWS_ROOT/$task" >&2
    return 1
  fi
  dirname "$hit"
}

# Every trial with both a meta entry and a transcription, comma separated. A meta
# row without a transcription has no gesture spans, so the runner cannot build a
# timestamp range for it.
trial_ids() {
  local root="$1" task="$2"
  awk 'NF >= 9 {print $1}' "$root/meta_file_${task}.txt" | while read -r trial; do
    [ -f "$root/transcriptions/${trial}.txt" ] && echo "$trial"
  done | paste -sd, -
}

TASKS=(Suturing Knot_Tying Needle_Passing)
declare -A ROOTS IDS COUNTS
for task in "${TASKS[@]}"; do
  ROOTS[$task]=$(resolve_root "$task")
  IDS[$task]=$(trial_ids "${ROOTS[$task]}" "$task")
  COUNTS[$task]=$(echo "${IDS[$task]}" | tr ',' '\n' | grep -c .)
  echo "$task: ${COUNTS[$task]} trials at ${ROOTS[$task]}"
done
TOTAL=$(( ${COUNTS[Suturing]} + ${COUNTS[Knot_Tying]} + ${COUNTS[Needle_Passing]} ))
echo "total trials: $TOTAL -> $(( TOTAL * 5 )) records per template family"

# This cluster does not isolate GPUs between jobs: another user's process can
# occupy a GPU Slurm handed us, which silently makes accelerate offload layers
# and then OOM mid-generation. Pin to GPUs that are actually free (<2GB used),
# waiting rather than dying if too few are.
NUM_FREE=0
FREE_GPUS=""
for attempt in $(seq 1 "${GPU_WAIT_TRIES:-60}"); do
  FREE_GPUS=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits | awk -F', ' '$2 < 2000 {print $1}' | paste -sd, -)
  NUM_FREE=$(echo "$FREE_GPUS" | tr ',' '\n' | grep -c .)
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

# D before C: D is the family with an established baseline, so a regression shows
# up against something known rather than against nothing.
run_family() {
  local family="$1" templates="$2" task="$3"
  local n_templates out expected have
  n_templates=$(echo "$templates" | tr ',' '\n' | grep -c .)
  out="$OUT_ROOT/$family/$task"
  expected=$(( ${COUNTS[$task]} * n_templates ))
  have=0
  [ -f "$out/qa_records.jsonl" ] && have=$(grep -c . "$out/qa_records.jsonl" || true)
  if [ "$have" -eq "$expected" ]; then
    echo "=== $family / $task === already complete ($have/$expected), skipping"
    return 0
  fi
  [ "$have" -gt 0 ] && echo "=== $family / $task === partial ($have/$expected), regenerating"
  echo "=== $family / $task === $expected records"
  python3 scripts/run_annotation_qa_jigsaws.py \
    --dataset-root "${ROOTS[$task]}" \
    --system-prompt "$SYSTEM_PROMPT" \
    --model-id "$MODEL_ID" \
    --task "$task" \
    --trial-ids "${IDS[$task]}" \
    --granularity video \
    --templates "$templates" \
    --max-new-tokens "$MAX_NEW_TOKENS" \
    --temperature "$TEMPERATURE" \
    --max-retries "$MAX_RETRIES" \
    --device-map auto \
    --backend real \
    --output-dir "$out"
}

for task in "${TASKS[@]}"; do run_family D "$D_TEMPLATES" "$task"; done
for task in "${TASKS[@]}"; do run_family C "$C_TEMPLATES" "$task"; done

# Concatenate per family. Written with python so the encoding is explicit and no
# BOM is added -- json.loads rejects a leading BOM outright.
for family in D C; do
  python3 - "$OUT_ROOT/$family" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
parts = sorted(root.rglob("qa_records.jsonl"))
lines = []
for p in parts:
    if p.parent == root:
        continue
    lines += [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
(root / "qa_records.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"{root.name}: combined {len(lines)} records from {len(parts)} task files")
PY
done

echo "done. check with: python3 scripts/check_template_d.py $OUT_ROOT/D $OUT_ROOT/C"
