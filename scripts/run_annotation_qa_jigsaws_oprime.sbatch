#!/usr/bin/env bash
#SBATCH --job-name=anno-qa-jigsaws
#SBATCH --partition=a6000
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=8
#SBATCH --mem=160G
#SBATCH --time=08:00:00
#SBATCH --output=/home/mairuili/annotation_qa/logs/%x-%j.out
#SBATCH --error=/home/mairuili/annotation_qa/logs/%x-%j.err

set -euo pipefail

WORK_DIR="${WORK_DIR:-/home/mairuili/annotation_qa}"
DATASET_ROOT="${DATASET_ROOT:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$WORK_DIR/system-prompt-A-D.md}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
TRIAL_ID="${TRIAL_ID:-Suturing_B001}"
TEMPLATES="${TEMPLATES:-A3,C1,C2,C3,C6,C7,D1,D2,D3,D4,D5}"
MAX_SPANS="${MAX_SPANS:-0}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-2048}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR/results/${SLURM_JOB_ID:-manual}_anno_${TRIAL_ID}}"

QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="${HF_HOME:-/mnt/sun/shared/datasets/surgical_skill/.cache/huggingface/mairuili}"
export TMPDIR="${TMPDIR:-/mnt/sun/shared/datasets/surgical_skill/.tmp/annotation_qa}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export PYTHONFAULTHANDLER=1

mkdir -p "$WORK_DIR/logs" "$OUTPUT_DIR" "$TMPDIR"
cd "$WORK_DIR"

# This cluster does not isolate GPUs between jobs: other users' processes can
# occupy GPUs Slurm hands us, which silently forces accelerate to squeeze or
# offload layers and later OOM mid-generation. Pin to GPUs that are actually
# free (<2GB used) and fail fast if too few remain.
FREE_GPUS=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits | awk -F', ' '$2 < 2000 {print $1}' | paste -sd, -)
NUM_FREE=$(echo "$FREE_GPUS" | tr ',' '\n' | grep -c .)
echo "free GPUs: [$FREE_GPUS] count=$NUM_FREE"
if [ "$NUM_FREE" -lt "${MIN_FREE_GPUS:-3}" ]; then
  echo "ERROR: only $NUM_FREE free GPUs (<${MIN_FREE_GPUS:-3}); refusing to start"
  exit 1
fi
export CUDA_VISIBLE_DEVICES="$FREE_GPUS"

python3 scripts/run_annotation_qa_jigsaws.py \
  --dataset-root "$DATASET_ROOT" \
  --system-prompt "$SYSTEM_PROMPT" \
  --model-id "$MODEL_ID" \
  --trial-id "$TRIAL_ID" \
  --templates "$TEMPLATES" \
  --max-spans "$MAX_SPANS" \
  --max-new-tokens "$MAX_NEW_TOKENS" \
  --device-map auto \
  --backend real \
  --output-dir "$OUTPUT_DIR"
