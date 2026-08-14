#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${RUN_DIR:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking}"
MODEL_PATH="${MODEL_PATH:-$RUN_DIR/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-$RUN_DIR/tools/llama.cpp/build/bin/llama-server}"
DATASET_BASE="${DATASET_BASE:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS}"
SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-A-D.md"
JIGSAWS_SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-jigsaws-v1.2.md"
TEMPLATES="D1,D2,D3,D4,D5"

if [ ! -x "$LLAMA_BIN" ]; then
  echo "llama-server is missing or not executable: $LLAMA_BIN" >&2
  exit 1
fi
if [ ! -f "$MODEL_PATH" ]; then
  echo "Model is missing: $MODEL_PATH" >&2
  exit 1
fi

run_task_batch() (
  set -euo pipefail
  local task=$1
  local trial_ids=$2
  local gpu=$3
  local port=$4
  local output_dir="$RUN_DIR/results/$task"
  local server_log="$RUN_DIR/logs/llama-server-$task.log"
  local batch_log="$RUN_DIR/logs/generate-$task.log"
  local host="http://127.0.0.1:$port"

  mkdir -p "$output_dir" "$RUN_DIR/logs"
  CUDA_VISIBLE_DEVICES="$gpu" "$LLAMA_BIN" \
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
    --reasoning-format deepseek \
    --reasoning-preserve \
    --host 127.0.0.1 \
    --port "$port" \
    > "$server_log" 2>&1 &
  local server_pid=$!
  echo "$server_pid" > "$RUN_DIR/llama-server-$task.pid"
  trap 'kill "$server_pid" 2>/dev/null || true' EXIT

  for _ in $(seq 1 180); do
    if curl -fsS "$host/health" 2>/dev/null | grep -q '"status":"ok"'; then
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      echo "llama.cpp server for $task exited during startup" >&2
      tail -100 "$server_log" >&2
      exit 1
    fi
    sleep 2
  done
  curl -fsS "$host/health" | grep -q '"status":"ok"'

  python3 "$RUN_DIR/scripts/run_annotation_qa_jigsaws.py" \
    --task "$task" \
    --dataset-root "$DATASET_BASE/$task" \
    --system-prompt "$SYSTEM_PROMPT" \
    --jigsaws-system-prompt "$JIGSAWS_SYSTEM_PROMPT" \
    --trial-ids "$trial_ids" \
    --templates "$TEMPLATES" \
    --granularity video \
    --backend real \
    --inference-engine llama \
    --llama-host "$host" \
    --temperature 0.7 \
    --max-new-tokens 6000 \
    --max-retries 3 \
    --output-dir "$output_dir" \
    > "$batch_log" 2>&1
)

run_task_batch \
  Suturing \
  "Suturing_B001,Suturing_B002,Suturing_B003,Suturing_B004,Suturing_B005" \
  0 18080 &
pid_suturing=$!

run_task_batch \
  Needle_Passing \
  "Needle_Passing_B001,Needle_Passing_B002,Needle_Passing_B003,Needle_Passing_B004,Needle_Passing_C001" \
  1 18081 &
pid_needle=$!

run_task_batch \
  Knot_Tying \
  "Knot_Tying_B001,Knot_Tying_B002,Knot_Tying_B003,Knot_Tying_B004,Knot_Tying_C001" \
  2 18082 &
pid_knot=$!

wait "$pid_suturing"
wait "$pid_needle"
wait "$pid_knot"

python3 "$RUN_DIR/scripts/check_template_d.py" \
  "$RUN_DIR/results/Suturing" \
  "$RUN_DIR/results/Needle_Passing" \
  "$RUN_DIR/results/Knot_Tying" \
  > "$RUN_DIR/logs/check-template-d.log" 2>&1

python3 "$RUN_DIR/scripts/extract_qa_pairs.py" \
  "$RUN_DIR/results/Suturing/qa_records.jsonl" \
  "$RUN_DIR/results/Needle_Passing/qa_records.jsonl" \
  "$RUN_DIR/results/Knot_Tying/qa_records.jsonl"

python3 "$RUN_DIR/scripts/extract_qa_by_template.py" \
  "$RUN_DIR/results/Suturing/qa_records.jsonl" \
  "$RUN_DIR/results/Needle_Passing/qa_records.jsonl" \
  "$RUN_DIR/results/Knot_Tying/qa_records.jsonl" \
  --output-dir "$RUN_DIR/results/by_template"
