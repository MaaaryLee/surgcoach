#!/usr/bin/env bash
#SBATCH --job-name=anno-qa-jigsaws
#SBATCH --partition=a6000
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=8
#SBATCH --mem=160G
#SBATCH --time=08:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

# Submit from $ENDPOINT/surgcoach (on megatron: /mnt/meg/$USER/surgcoach), not your home
# directory -- SLURM writes the --output/--error files above into whatever
# directory you run `sbatch` from, and job logs in $HOME hit permission errors
# on this cluster. Requires $ENDPOINT to already be set in ~/.bashrc.

set -euo pipefail

WORK_DIR="${WORK_DIR:-$PWD}"
DATASET_ROOT="${DATASET_ROOT:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$WORK_DIR/Prompts_And_Pipeline/system-prompt-A-D.md}"
JIGSAWS_SYSTEM_PROMPT="${JIGSAWS_SYSTEM_PROMPT:-$WORK_DIR/Prompts_And_Pipeline/system-prompt-jigsaws-v1.2.md}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
TRIAL_ID="${TRIAL_ID:-Suturing_B001}"
TRIAL_IDS="${TRIAL_IDS:-}"
TRIAL_TEMPLATE_PAIRS="${TRIAL_TEMPLATE_PAIRS:-}"
TASK="${TASK:-Suturing}"
GRANULARITY="${GRANULARITY:-video}"
TEMPLATES="${TEMPLATES:-D1}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-6000}"
# Sampling instead of greedy decoding: records from the same trial share the
# same underlying GRS scores, and greedy decoding was observed converging on
# near-identical phrasing across them. 0.7 trades exact reproducibility for
# lexical variety, which is the right trade when building a dataset.
TEMPERATURE="${TEMPERATURE:-0.7}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR/results/${SLURM_JOB_ID:-manual}_anno_${TRIAL_ID}}"

# Prebuilt torch/transformers for Qwen3.6 -- verify this still exists before
# submitting (`ls /mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages`).
# If missing, install per requirements-annotation.txt into your own $ENDPOINT instead.
QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Triton (used by the fla package inside Qwen3.6's MoE modeling code) JIT-compiles
# a small C helper at runtime and needs Python.h, which this cluster doesn't have
# installed system-wide. Extract it yourself with (no root needed):
#   cd $ENDPOINT && mkdir pydev && cd pydev && apt download libpython3.10-dev
#   dpkg -x libpython3.10-dev*.deb .
PYDEV_ROOT="${PYDEV_ROOT:-$ENDPOINT/pydev/usr/include}"
export CPATH="$PYDEV_ROOT/python3.10:$PYDEV_ROOT/x86_64-linux-gnu/python3.10:$PYDEV_ROOT${CPATH:+:$CPATH}"

export HF_HOME="${HF_HOME:-$ENDPOINT/.cache/huggingface}"
export TMPDIR="${TMPDIR:-$ENDPOINT/.tmp/annotation_qa}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export PYTHONFAULTHANDLER=1

mkdir -p "$OUTPUT_DIR" "$TMPDIR" "$HF_HOME"
cd "$WORK_DIR"

# This cluster does not isolate GPUs between jobs: other users' processes can
# occupy GPUs Slurm hands us, which silently forces accelerate to squeeze or
# offload layers and later OOM mid-generation. Pin to GPUs that are actually
# free (<2GB used). If too few are free, wait and re-check every 5 minutes
# (up to GPU_WAIT_TRIES polls) instead of dying. Adopted from
# run_annotation_qa_jigsaws_oprime.sbatch.
NUM_FREE=0
FREE_GPUS=""
for attempt in $(seq 1 "${GPU_WAIT_TRIES:-60}"); do
  FREE_GPUS=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits | awk -F', ' '$2 < 2000 {print $1}' | paste -sd, -)
  NUM_FREE=$(echo "$FREE_GPUS" | tr ',' '\n' | grep -c .)
  if [ "$NUM_FREE" -ge "${MIN_FREE_GPUS:-3}" ]; then
    break
  fi
  echo "poll $attempt/${GPU_WAIT_TRIES:-60}: only $NUM_FREE free GPUs (<${MIN_FREE_GPUS:-3}); sleeping 300s"
  sleep 300
done
echo "free GPUs: [$FREE_GPUS] count=$NUM_FREE"
if [ "$NUM_FREE" -lt "${MIN_FREE_GPUS:-3}" ]; then
  echo "ERROR: still only $NUM_FREE free GPUs after waiting; giving up"
  exit 1
fi
export CUDA_VISIBLE_DEVICES="$FREE_GPUS"

RUN_ARGS=(
  --task "$TASK"
  --dataset-root "$DATASET_ROOT" \
  --system-prompt "$SYSTEM_PROMPT" \
  --jigsaws-system-prompt "$JIGSAWS_SYSTEM_PROMPT" \
  --model-id "$MODEL_ID" \
  --trial-id "$TRIAL_ID" \
  --templates "$TEMPLATES" \
  --granularity "$GRANULARITY" \
  --max-new-tokens "$MAX_NEW_TOKENS" \
  --temperature "$TEMPERATURE" \
  --device-map auto \
  --backend real \
  --output-dir "$OUTPUT_DIR"
)

if [ -n "$TRIAL_IDS" ]; then
  RUN_ARGS+=(--trial-ids "$TRIAL_IDS")
fi
if [ -n "$TRIAL_TEMPLATE_PAIRS" ]; then
  RUN_ARGS+=(--trial-template-pairs "$TRIAL_TEMPLATE_PAIRS")
fi

python3 scripts/run_annotation_qa_jigsaws.py "${RUN_ARGS[@]}"
