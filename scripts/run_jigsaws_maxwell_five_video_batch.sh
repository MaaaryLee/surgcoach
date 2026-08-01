#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${RUN_DIR:-/home/mairuili/surgcoach_runs/2026-07-28-five-video-d1-d5}"
MODEL_PATH="${MODEL_PATH:-$RUN_DIR/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
MODEL_PART="${MODEL_PART:-$MODEL_PATH.part}"
LLAMA_BIN="${LLAMA_BIN:-$HOME/.local/bin/llama}"
LLAMA_HOST="${LLAMA_HOST:-http://127.0.0.1:18080}"
GPU_DEVICE="${GPU_DEVICE:-1}"
DOWNLOAD_PID_FILE="${DOWNLOAD_PID_FILE:-$RUN_DIR/model-download.pid}"
SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-A-D.md"
JIGSAWS_SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-jigsaws-v1.2.md"

if [ -f "$DOWNLOAD_PID_FILE" ]; then
  download_pid=$(cat "$DOWNLOAD_PID_FILE")
  while kill -0 "$download_pid" 2>/dev/null; do
    sleep 30
  done
fi

if [ ! -f "$MODEL_PATH" ]; then
  if [ ! -s "$MODEL_PART" ]; then
    echo "Model download is missing: $MODEL_PART" >&2
    exit 1
  fi
  expected_size=20419565568
  actual_size=$(stat -c %s "$MODEL_PART")
  if [ "$actual_size" -ne "$expected_size" ]; then
    echo "Model download is incomplete: $actual_size of $expected_size bytes" >&2
    exit 1
  fi
  mv "$MODEL_PART" "$MODEL_PATH"
fi

llama_command=("$LLAMA_BIN")
if [ "$(basename "$LLAMA_BIN")" != "llama-server" ]; then
  llama_command+=(serve)
fi

CUDA_VISIBLE_DEVICES="$GPU_DEVICE" "${llama_command[@]}" \
  --model "$MODEL_PATH" \
  --no-mmproj \
  --split-mode none \
  --main-gpu 0 \
  --gpu-layers all \
  --no-repack \
  --fit-target 4096 \
  --ctx-size 16384 \
  --parallel 1 \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --reasoning-preserve \
  --host 127.0.0.1 \
  --port 18080 \
  > "$RUN_DIR/llama-server.log" 2>&1 &
server_pid=$!
echo "$server_pid" > "$RUN_DIR/llama-server.pid"
trap 'kill "$server_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 120); do
  if curl -fsS "$LLAMA_HOST/health" | grep -q '"status":"ok"'; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "llama.cpp server exited during startup" >&2
    tail -100 "$RUN_DIR/llama-server.log" >&2
    exit 1
  fi
  sleep 5
done
curl -fsS "$LLAMA_HOST/health" | grep -q '"status":"ok"'

run_task() {
  local task=$1
  local pairs=$2
  local output_dir="$RUN_DIR/results/$task"
  mkdir -p "$output_dir"
  python3 "$RUN_DIR/scripts/run_annotation_qa_jigsaws.py" \
    --task "$task" \
    --dataset-root "$RUN_DIR/datasets/$task" \
    --system-prompt "$SYSTEM_PROMPT" \
    --jigsaws-system-prompt "$JIGSAWS_SYSTEM_PROMPT" \
    --trial-template-pairs "$pairs" \
    --granularity video \
    --backend real \
    --inference-engine llama \
    --llama-host "$LLAMA_HOST" \
    --temperature 0.7 \
    --max-new-tokens 6000 \
    --max-retries 2 \
    --output-dir "$output_dir"
}

run_task Suturing \
  "Suturing_B001:D1,Suturing_B002:D2,Suturing_B003:D3,Suturing_B004:D4,Suturing_B005:D5"
run_task Needle_Passing \
  "Needle_Passing_B001:D1,Needle_Passing_B002:D2,Needle_Passing_B003:D3,Needle_Passing_B004:D4,Needle_Passing_C001:D5"
run_task Knot_Tying \
  "Knot_Tying_B001:D1,Knot_Tying_B002:D2,Knot_Tying_B003:D3,Knot_Tying_B004:D4,Knot_Tying_C001:D5"

python3 "$RUN_DIR/scripts/extract_qa_pairs.py" \
  "$RUN_DIR/results/Suturing/qa_records.jsonl" \
  "$RUN_DIR/results/Needle_Passing/qa_records.jsonl" \
  "$RUN_DIR/results/Knot_Tying/qa_records.jsonl"
