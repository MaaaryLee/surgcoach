#!/usr/bin/env bash
#SBATCH --job-name=surgcoach-eval-smoke
#SBATCH --partition=a6000
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=120G
#SBATCH --time=08:00:00
#SBATCH --output=/mnt/sun/shared/datasets/surgical_skill/work/surgical-error-detection/logs/%x-%j.out
#SBATCH --error=/mnt/sun/shared/datasets/surgical_skill/work/surgical-error-detection/logs/%x-%j.err

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIRST_RUN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd -- "$FIRST_RUN_DIR/.." && pwd)"
WORK_DIR="${WORK_DIR:-$REPO_DIR}"
GEN_OUTPUT_DIR="${GEN_OUTPUT_DIR:?Set GEN_OUTPUT_DIR to the generation smoke output directory}"
EVAL_OUTPUT_DIR="${EVAL_OUTPUT_DIR:-outputs/evaluator_smoke}"
RUNNER_SCRIPT_DIR="${RUNNER_SCRIPT_DIR:-$SCRIPT_DIR}"
MODEL_ID="${MODEL_ID:-google/gemma-4-31B-it}"
MAX_RECORDS="${MAX_RECORDS:-2}"
MIN_VALID="${MIN_VALID:-2}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-256}"

QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
QWEN25_PACKAGE_DIR="${QWEN25_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen25vl/site-packages}"
export PYTHONPATH="$QWEN36_PACKAGE_DIR:$QWEN25_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="${HF_HOME:-/mnt/sun/shared/datasets/surgical_skill/.cache/huggingface/mairuili}"
export TMPDIR="${TMPDIR:-/mnt/sun/shared/datasets/surgical_skill/.tmp/gemma4}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p "$WORK_DIR/logs" "$WORK_DIR/$EVAL_OUTPUT_DIR" "$TMPDIR"
cd "$WORK_DIR"

python3 "$RUNNER_SCRIPT_DIR/run_gemma4_evaluator_smoke.py" \
  --generated-jsonl "$GEN_OUTPUT_DIR/generated_qa_shard0.jsonl" \
  --llm-output-jsonl "$EVAL_OUTPUT_DIR/llm_verified_smoke.jsonl" \
  --vlm-output-jsonl "$EVAL_OUTPUT_DIR/vlm_verified_smoke.jsonl" \
  --rejected-jsonl "$EVAL_OUTPUT_DIR/rejected_evaluator_smoke.jsonl" \
  --model-id "$MODEL_ID" \
  --max-records "$MAX_RECORDS" \
  --min-valid "$MIN_VALID" \
  --max-new-tokens "$MAX_NEW_TOKENS" \
  --device-map auto
