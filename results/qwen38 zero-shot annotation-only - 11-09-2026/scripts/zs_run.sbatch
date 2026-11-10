#!/bin/bash
#SBATCH --account=gedas
#SBATCH --partition=a6000
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=12:00:00
#SBATCH --job-name=q38-zeroshot
#SBATCH --output=logs/q38-zeroshot-%j.out
# Qwen3.8-27B zero-shot: one answer per unit, then LLM rubric v5 review, on one GPU.
# UNITS="units/a.jsonl units/b.jsonl" PHASE=both sbatch --chdir=<run dir> scripts/zs_run.sbatch
set -euo pipefail
WORK_DIR="${WORK_DIR:-$PWD}"
MODEL_PATH="${MODEL_PATH:-/mnt/opr/mairuili/models/Qwen3.8-27B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking/tools/llama.cpp/build/bin/llama-server}"
LLAMA_PORT="${LLAMA_PORT:-$(( 19000 + ${SLURM_JOB_ID:-0} % 1000 ))}"
LLAMA_HOST="http://127.0.0.1:$LLAMA_PORT"
OUT_DIR="${OUT_DIR:-$WORK_DIR/outputs/job-${SLURM_JOB_ID:-manual}}"
PARALLEL="${PARALLEL:-4}"
mkdir -p "$OUT_DIR"; cd "$WORK_DIR"
export CUDA_DEVICE_ORDER=PCI_BUS_ID
NEED_MIB="${NEED_MIB:-34000}"
pick_gpu() {
  local assigned="${CUDA_VISIBLE_DEVICES%%,*}" free
  if [[ -n "$assigned" ]]; then
    free=$(nvidia-smi --id="$assigned" --query-gpu=memory.free --format=csv,noheader,nounits | tr -d ' ')
    if [[ "${free:-0}" -ge "$NEED_MIB" ]]; then echo "$assigned"; return; fi
  fi
  nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv,noheader,nounits \
    | awk -F', ' -v need="$NEED_MIB" '$2 < 2000 && $3 >= need && pick == "" {pick = $1} END {if (pick != "") print pick}'
}
GPU=""
for attempt in $(seq 1 30); do GPU="$(pick_gpu)"; [[ -n "$GPU" ]] && break; echo "poll $attempt: no free GPU"; sleep 60; done
[[ -n "$GPU" ]] || { echo "ERROR: no GPU free"; exit 1; }
echo "node $(hostname) GPU $GPU units: $UNITS"
export CUDA_VISIBLE_DEVICES="$GPU"
"$LLAMA_BIN" --model "$MODEL_PATH" --no-mmproj --gpu-layers all \
  --ctx-size $(( PARALLEL * 20480 )) --parallel "$PARALLEL" --reasoning-format deepseek \
  --host 127.0.0.1 --port "$LLAMA_PORT" > "$OUT_DIR/llama-server.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 300); do
  curl -fsS "$LLAMA_HOST/health" 2>/dev/null | grep -q '"status":"ok"' && break
  kill -0 "$server_pid" 2>/dev/null || { tail -60 "$OUT_DIR/llama-server.log"; exit 1; }
  sleep 2
done
python3 -I scripts/zs_run.py --units $UNITS --out "$OUT_DIR" --host "$LLAMA_HOST" \
  --workers "$PARALLEL" --phase "${PHASE:-both}"
