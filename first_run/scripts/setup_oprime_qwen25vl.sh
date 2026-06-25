#!/usr/bin/env bash
set -euo pipefail

ENV_DIR="${ENV_DIR:-/mnt/sun/shared/datasets/surgical_skill/.envs/qwen25vl}"
PACKAGE_DIR="${PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen25vl/site-packages}"
TMPDIR="${TMPDIR:-/mnt/sun/shared/datasets/surgical_skill/.tmp/qwen25vl}"
PIP_CACHE_DIR="${PIP_CACHE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.cache/pip/mairuili-qwen25vl}"
HF_HOME="${HF_HOME:-/mnt/sun/shared/datasets/surgical_skill/.cache/huggingface/mairuili}"
REPO_DIR="${REPO_DIR:-$PWD}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen2.5-VL-7B-Instruct}"

mkdir -p "$ENV_DIR" "$PACKAGE_DIR" "$TMPDIR" "$PIP_CACHE_DIR" "$HF_HOME"
export TMPDIR PIP_CACHE_DIR

USE_VENV=1
if [ ! -x "$ENV_DIR/bin/python" ] || [ ! -x "$ENV_DIR/bin/pip" ]; then
  if ! python3 -m venv "$ENV_DIR"; then
    USE_VENV=0
  fi
  if [ ! -x "$ENV_DIR/bin/pip" ]; then
    USE_VENV=0
  fi
fi

if [ "$USE_VENV" -eq 1 ]; then
  # shellcheck disable=SC1091
  source "$ENV_DIR/bin/activate"

  python -m pip install --upgrade pip setuptools wheel
  python -m pip install --extra-index-url https://download.pytorch.org/whl/cu124 \
    -r "$REPO_DIR/requirements-qwen25vl.txt"
  PYTHON_BIN="python"
else
  echo "python3 -m venv is unavailable or incomplete; using PACKAGE_DIR with PYTHONPATH instead."
  python3 -m pip install --upgrade --target "$PACKAGE_DIR" pip setuptools wheel
  export PYTHONPATH="$PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
  python3 -m pip install --upgrade --target "$PACKAGE_DIR" \
    --extra-index-url https://download.pytorch.org/whl/cu124 \
    -r "$REPO_DIR/requirements-qwen25vl.txt"
  PYTHON_BIN="python3"
fi

export HF_HOME
"$PYTHON_BIN" - <<PY
from huggingface_hub import snapshot_download

model_id = "${MODEL_ID}"
path = snapshot_download(repo_id=model_id)
print(f"Downloaded {model_id} to {path}")
PY

"$PYTHON_BIN" - <<'PY'
import torch
import transformers
import qwen_vl_utils
import decord

print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
print("transformers", transformers.__version__)
print("qwen_vl_utils", getattr(qwen_vl_utils, "__version__", "unknown"))
print("decord", decord.__version__)
PY
