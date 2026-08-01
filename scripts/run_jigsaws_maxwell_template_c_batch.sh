#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${RUN_DIR:-/home/mairuili/surgcoach_runs/2026-07-28-c105-annotation-first}"
SOURCE_RUN_DIR="${SOURCE_RUN_DIR:-/home/mairuili/surgcoach_runs/2026-07-28-five-video-d1-d5}"
MODEL_PATH="${MODEL_PATH:-$SOURCE_RUN_DIR/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-$SOURCE_RUN_DIR/llama.cpp/build/bin/llama-server}"
LLAMA_HOST="${LLAMA_HOST:-http://127.0.0.1:18081}"
GPU_DEVICE="${GPU_DEVICE:-0}"
SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-A-D.md"
JIGSAWS_SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-jigsaws-v1.2.md"
TEMPLATES="C1,C2,C3,C4,C5,C6,C7"

CUDA_VISIBLE_DEVICES="$GPU_DEVICE" "$LLAMA_BIN" \
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
  --port 18081 \
  > "$RUN_DIR/llama-server.log" 2>&1 &
server_pid=$!
echo "$server_pid" > "$RUN_DIR/llama-server.pid"
trap 'kill "$server_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 120); do
  if curl -fsS "$LLAMA_HOST/health" 2>/dev/null | grep -q '"status":"ok"'; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    tail -100 "$RUN_DIR/llama-server.log" >&2
    exit 1
  fi
  sleep 2
done
curl -fsS "$LLAMA_HOST/health" | grep -q '"status":"ok"'

run_task() {
  local task=$1
  local trial_ids=$2
  local output_dir="$RUN_DIR/results/$task"
  mkdir -p "$output_dir"
  python3 "$RUN_DIR/scripts/run_annotation_qa_jigsaws.py" \
    --task "$task" \
    --dataset-root "$RUN_DIR/datasets/$task" \
    --system-prompt "$SYSTEM_PROMPT" \
    --jigsaws-system-prompt "$JIGSAWS_SYSTEM_PROMPT" \
    --trial-ids "$trial_ids" \
    --templates "$TEMPLATES" \
    --granularity video \
    --backend real \
    --inference-engine llama \
    --llama-host "$LLAMA_HOST" \
    --temperature 0.7 \
    --max-new-tokens 6000 \
    --max-retries 3 \
    --output-dir "$output_dir"
}

run_task Suturing \
  "Suturing_B001,Suturing_B002,Suturing_B003,Suturing_B004,Suturing_B005"
run_task Needle_Passing \
  "Needle_Passing_B001,Needle_Passing_B002,Needle_Passing_B003,Needle_Passing_B004,Needle_Passing_C001"
run_task Knot_Tying \
  "Knot_Tying_B001,Knot_Tying_B002,Knot_Tying_B003,Knot_Tying_B004,Knot_Tying_C001"

python3 "$RUN_DIR/scripts/extract_qa_by_template.py" \
  "$RUN_DIR/results/Suturing/qa_records.jsonl" \
  "$RUN_DIR/results/Needle_Passing/qa_records.jsonl" \
  "$RUN_DIR/results/Knot_Tying/qa_records.jsonl" \
  --output-dir "$RUN_DIR/results/by_template"
