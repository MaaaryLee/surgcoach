#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${RUN_DIR:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-b1345-thinking}"
D75_RUN="${D75_RUN:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking}"
MODEL_PATH="${MODEL_PATH:-$D75_RUN/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-$D75_RUN/tools/llama.cpp/build/bin/llama-server}"
GPU="${GPU:-2}"
PORT="${PORT:-18213}"
SELECTION_ID="${SELECTION_ID:-b3_bern_02}"
TEMPLATE_ID="${TEMPLATE_ID:-B3}"
OUTPUT_DIR="${OUTPUT_DIR:-$RUN_DIR/results/recovery-$SELECTION_ID}"
HOST="http://127.0.0.1:$PORT"
SERVER_LOG="$RUN_DIR/logs/llama-server-recovery-$SELECTION_ID.log"

CUDA_VISIBLE_DEVICES="$GPU" "$LLAMA_BIN" \
  --model "$MODEL_PATH" \
  --no-mmproj \
  --split-mode none \
  --main-gpu 0 \
  --gpu-layers all \
  --no-repack \
  --fit-target 4096 \
  --ctx-size 32768 \
  --parallel 1 \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --reasoning-format deepseek \
  --reasoning-preserve \
  --host 127.0.0.1 \
  --port "$PORT" \
  > "$SERVER_LOG" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 180); do
  if curl -fsS "$HOST/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    tail -100 "$SERVER_LOG" >&2
    exit 1
  fi
  sleep 2
done
curl -fsS "$HOST/health" >/dev/null

python3 "$RUN_DIR/scripts/run_annotation_qa_b1345.py" \
  --manifest "$RUN_DIR/Prompts_And_Pipeline/b1345-selected-clips.json" \
  --system-prompt "$RUN_DIR/Prompts_And_Pipeline/system-prompt-A-D.md" \
  --template-questions "$RUN_DIR/Prompts_And_Pipeline/template questions.md" \
  --templates "$TEMPLATE_ID" \
  --selection-ids "$SELECTION_ID" \
  --backend real \
  --inference-engine llama \
  --llama-host "$HOST" \
  --temperature 0.7 \
  --max-new-tokens 7000 \
  --max-retries 3 \
  --output-dir "$OUTPUT_DIR"
