#!/usr/bin/env bash
#SBATCH --job-name=surgcoach-d1-qwen36
#SBATCH --partition=a6000
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=8
#SBATCH --mem=120G
#SBATCH --time=16:00:00
#SBATCH --output=/mnt/sun/shared/datasets/surgical_skill/work/surgical-error-detection/logs/%x-%j.out
#SBATCH --error=/mnt/sun/shared/datasets/surgical_skill/work/surgical-error-detection/logs/%x-%j.err

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIRST_RUN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd -- "$FIRST_RUN_DIR/.." && pwd)"
WORK_DIR="${WORK_DIR:-$REPO_DIR}"
SHARD_INDEX="${SHARD_INDEX:?Set SHARD_INDEX to 0 or 1}"
NUM_SHARDS="${NUM_SHARDS:-2}"
MAX_EXAMPLES="${MAX_EXAMPLES:-0}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
OUTPUT_DIR="${OUTPUT_DIR:-outputs/new_pipeline}"
CAPTURES="${CAPTURES:-capture1,capture2}"
GENERATION_CAPTURES="${GENERATION_CAPTURES:-capture1}"
CANDIDATE_UNIT="${CANDIDATE_UNIT:-frame}"
FRAME_STRIDE="${FRAME_STRIDE:-1}"
FRAME_CONTEXT_RADIUS="${FRAME_CONTEXT_RADIUS:-0}"
NUM_FRAMES="${NUM_FRAMES:-4}"
RUNNER_SCRIPT_DIR="${RUNNER_SCRIPT_DIR:-$SCRIPT_DIR}"
MIN_VALID="${MIN_VALID:-1}"
RUNNER_EXTRA_ARGS="${RUNNER_EXTRA_ARGS:-}"

QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
QWEN25_PACKAGE_DIR="${QWEN25_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen25vl/site-packages}"
export PYTHONPATH="$QWEN36_PACKAGE_DIR:$QWEN25_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="${HF_HOME:-/mnt/sun/shared/datasets/surgical_skill/.cache/huggingface/mairuili}"
export TMPDIR="${TMPDIR:-/mnt/sun/shared/datasets/surgical_skill/.tmp/qwen36}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p "$WORK_DIR/logs" "$WORK_DIR/$OUTPUT_DIR" "$TMPDIR"
cd "$WORK_DIR"

python3 "$RUNNER_SCRIPT_DIR/run_jigsaws_d1_qwen36_batch.py" \
  --dataset-root /mnt/sun/shared/datasets/surgical_skill/JIGSAWS/Suturing \
  --captures "$CAPTURES" \
  --generation-captures "$GENERATION_CAPTURES" \
  --model-id "$MODEL_ID" \
  --max-examples "$MAX_EXAMPLES" \
  --candidate-unit "$CANDIDATE_UNIT" \
  --frame-stride "$FRAME_STRIDE" \
  --frame-context-radius "$FRAME_CONTEXT_RADIUS" \
  --num-shards "$NUM_SHARDS" \
  --shard-index "$SHARD_INDEX" \
  --num-frames "$NUM_FRAMES" \
  --max-new-tokens 512 \
  --device-map auto \
  --min-valid "$MIN_VALID" \
  --output-jsonl "$OUTPUT_DIR/generated_qa_shard${SHARD_INDEX}.jsonl" \
  --rejected-jsonl "$OUTPUT_DIR/rejected_qa_shard${SHARD_INDEX}.jsonl" \
  --frame-output-dir "$OUTPUT_DIR/frames_shard${SHARD_INDEX}" \
  $RUNNER_EXTRA_ARGS
