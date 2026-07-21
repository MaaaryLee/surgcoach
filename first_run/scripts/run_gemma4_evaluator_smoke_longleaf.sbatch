#!/usr/bin/env bash
#SBATCH --job-name=surgcoach-eval-smoke
#SBATCH --partition=l40-gpu
#SBATCH --gres=gpu:2
# NOTE: pass the Slurm account for THIS project at submit time:
#   sbatch --account=<your-project-account> <this-file>
#SBATCH --qos=gpu_access
#SBATCH --cpus-per-task=8
#SBATCH --mem=128G
#SBATCH --time=08:00:00
#SBATCH --output=/nas/longleaf/home/mairuili/surgical-error-detection/logs/%x-%j.out
#SBATCH --error=/nas/longleaf/home/mairuili/surgical-error-detection/logs/%x-%j.err

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIRST_RUN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd -- "$FIRST_RUN_DIR/.." && pwd)"
WORK_DIR="${WORK_DIR:-$REPO_DIR}"
ENV_DIR="${ENV_DIR:-$WORK_DIR/.venv-qwen36}"
GEN_OUTPUT_DIR="${GEN_OUTPUT_DIR:?Set GEN_OUTPUT_DIR to the generation smoke output directory}"
EVAL_OUTPUT_DIR="${EVAL_OUTPUT_DIR:-outputs/evaluator_smoke_longleaf}"
RUNNER_SCRIPT_DIR="${RUNNER_SCRIPT_DIR:-$SCRIPT_DIR}"
MODEL_ID="${MODEL_ID:-google/gemma-4-31B-it}"
MAX_RECORDS="${MAX_RECORDS:-2}"
MIN_VALID="${MIN_VALID:-2}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-256}"

export HF_HOME="${HF_HOME:-$WORK_DIR/.cache/huggingface}"
export TMPDIR="${TMPDIR:-$WORK_DIR/.tmp}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p "$WORK_DIR/logs" "$WORK_DIR/$EVAL_OUTPUT_DIR" "$TMPDIR"
cd "$WORK_DIR"

if command -v module >/dev/null 2>&1; then
  module load python/3.12.4 cuda/12.4 || true
fi

# shellcheck disable=SC1091
source "$ENV_DIR/bin/activate"

python "$RUNNER_SCRIPT_DIR/run_gemma4_evaluator_smoke.py" \
  --generated-jsonl "$GEN_OUTPUT_DIR/generated_qa_shard0.jsonl" \
  --llm-output-jsonl "$EVAL_OUTPUT_DIR/llm_verified_smoke.jsonl" \
  --vlm-output-jsonl "$EVAL_OUTPUT_DIR/vlm_verified_smoke.jsonl" \
  --rejected-jsonl "$EVAL_OUTPUT_DIR/rejected_evaluator_smoke.jsonl" \
  --model-id "$MODEL_ID" \
  --max-records "$MAX_RECORDS" \
  --min-valid "$MIN_VALID" \
  --max-new-tokens "$MAX_NEW_TOKENS" \
  --device-map auto
