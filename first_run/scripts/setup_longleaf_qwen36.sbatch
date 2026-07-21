#!/usr/bin/env bash
#SBATCH --job-name=surgcoach-qwen36-setup
#SBATCH --partition=general
# NOTE: pass the Slurm account for THIS project at submit time:
#   sbatch --account=<your-project-account> <this-file>
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --output=/nas/longleaf/home/mairuili/surgical-error-detection/logs/%x-%j.out
#SBATCH --error=/nas/longleaf/home/mairuili/surgical-error-detection/logs/%x-%j.err

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIRST_RUN_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd -- "$FIRST_RUN_DIR/.." && pwd)"
WORK_DIR="${WORK_DIR:-$REPO_DIR}"
ENV_DIR="${ENV_DIR:-$WORK_DIR/.venv-qwen36}"
HF_HOME="${HF_HOME:-$WORK_DIR/.cache/huggingface}"
PIP_CACHE_DIR="${PIP_CACHE_DIR:-$WORK_DIR/.cache/pip}"

mkdir -p "$WORK_DIR/logs" "$HF_HOME" "$PIP_CACHE_DIR"
cd "$WORK_DIR"

if command -v module >/dev/null 2>&1; then
  module load python/3.12.4 cuda/12.4 || true
fi

if [ ! -x "$ENV_DIR/bin/python" ]; then
  python3 -m venv "$ENV_DIR"
fi

# shellcheck disable=SC1091
source "$ENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install --extra-index-url https://download.pytorch.org/whl/cu124 \
  torch==2.6.0+cu124 torchvision==0.21.0+cu124
python -m pip install \
  accelerate decord "huggingface_hub>=0.30.0" numpy pillow "qwen-vl-utils[decord]==0.0.8" \
  regex safetensors sentencepiece "tokenizers>=0.22,<0.23.1" protobuf
python -m pip install --no-deps "git+https://github.com/huggingface/transformers.git"

export HF_HOME PIP_CACHE_DIR
python - <<'PY'
from huggingface_hub import snapshot_download
from transformers import AutoConfig, AutoProcessor

model_id = "Qwen/Qwen3.6-35B-A3B"
config = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
print("config", type(config), getattr(config, "model_type", None))
processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
print("processor", type(processor))
path = snapshot_download(repo_id=model_id)
print("downloaded", path)
PY

python - <<'PY'
import torch
import torchvision
import transformers
import decord
import qwen_vl_utils

print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("torchvision", torchvision.__version__)
print("transformers", transformers.__version__)
print("decord", decord.__version__)
print("qwen_vl_utils", getattr(qwen_vl_utils, "__version__", "unknown"))
PY
