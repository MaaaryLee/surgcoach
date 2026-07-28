#!/usr/bin/env bash
#SBATCH --job-name=anno-qa-endoscapes
#SBATCH --partition=a6000
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=8
#SBATCH --mem=160G
#SBATCH --time=08:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

# Submit from $ENDPOINT/surgcoach (e.g. /mnt/meg/$USER/surgcoach), not your home
# directory -- SLURM writes the --output/--error files above into whatever
# directory you run `sbatch` from, and job logs in $HOME hit permission errors
# on this cluster. Requires $ENDPOINT to already be set in ~/.bashrc.

set -euo pipefail

WORK_DIR="${WORK_DIR:-$PWD}"
METADATA_CSV="${METADATA_CSV:-/mnt/sun/shared/datasets/surgical_skill/Endoscapes2023/all_metadata.csv}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$WORK_DIR/Prompts_And_Pipeline/system-prompt-A-D.md}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
VIDEO_ID="${VIDEO_ID:-1}"
TEMPLATES="${TEMPLATES:-B7}"
MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-6000}"
# Sampling instead of greedy decoding: records with similar underlying labels
# can converge on near-identical phrasing under greedy decoding. 0.7 trades
# exact reproducibility for lexical variety, the right trade for a dataset.
TEMPERATURE="${TEMPERATURE:-0.7}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR/results/${SLURM_JOB_ID:-manual}_anno_endoscapes_${VIDEO_ID}}"

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

python3 scripts/run_annotation_qa_endoscapes2023.py \
  --metadata-csv "$METADATA_CSV" \
  --system-prompt "$SYSTEM_PROMPT" \
  --model-id "$MODEL_ID" \
  --video-id "$VIDEO_ID" \
  --templates "$TEMPLATES" \
  --max-new-tokens "$MAX_NEW_TOKENS" \
  --temperature "$TEMPERATURE" \
  --device-map auto \
  --backend real \
  --output-dir "$OUTPUT_DIR"
