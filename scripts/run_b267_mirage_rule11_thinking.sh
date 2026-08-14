#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${RUN_DIR:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-b267-rule11-thinking}"
D75_RUN="${D75_RUN:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking}"
MODEL_PATH="${MODEL_PATH:-$D75_RUN/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-$D75_RUN/tools/llama.cpp/build/bin/llama-server}"
SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-A-D.md"
TEMPLATE_QUESTIONS="$RUN_DIR/Prompts_And_Pipeline/template questions.md"
MANIFEST="$RUN_DIR/Prompts_And_Pipeline/b267-selected-clips.json"

run_template_batch() (
  set -euo pipefail
  local template_id=$1
  local gpu=$2
  local port=$3
  local output_dir="$RUN_DIR/results/$template_id"
  local server_log="$RUN_DIR/logs/llama-server-$template_id.log"
  local batch_log="$RUN_DIR/logs/generate-$template_id.log"
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
  trap 'kill "$server_pid" 2>/dev/null || true' EXIT

  for _ in $(seq 1 180); do
    if curl -fsS "$host/health" >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      tail -100 "$server_log" >&2
      exit 1
    fi
    sleep 2
  done
  curl -fsS "$host/health" >/dev/null

  python3 "$RUN_DIR/scripts/run_annotation_qa_b267.py" \
    --manifest "$MANIFEST" \
    --system-prompt "$SYSTEM_PROMPT" \
    --template-questions "$TEMPLATE_QUESTIONS" \
    --templates "$template_id" \
    --backend real \
    --inference-engine llama \
    --llama-host "$host" \
    --temperature 0.7 \
    --max-new-tokens 6000 \
    --max-retries 3 \
    --output-dir "$output_dir" \
    > "$batch_log" 2>&1
)

run_template_batch B2 4 18302 &
pid_b2=$!
run_template_batch B6 5 18306 &
pid_b6=$!
run_template_batch B7 6 18307 &
pid_b7=$!

wait "$pid_b2"
wait "$pid_b6"
wait "$pid_b7"

python3 "$RUN_DIR/scripts/check_template_b267.py" \
  "$RUN_DIR/results/B2/qa_records.jsonl" \
  "$RUN_DIR/results/B6/qa_records.jsonl" \
  "$RUN_DIR/results/B7/qa_records.jsonl" \
  --manifest "$MANIFEST" \
  --template-questions "$TEMPLATE_QUESTIONS" \
  > "$RUN_DIR/logs/check-b267.log" 2>&1

mkdir -p "$RUN_DIR/results/all"
: > "$RUN_DIR/results/all/qa_records.jsonl"
for template_id in B2 B6 B7; do
  cat "$RUN_DIR/results/$template_id/qa_records.jsonl" \
    >> "$RUN_DIR/results/all/qa_records.jsonl"
done

python3 "$RUN_DIR/scripts/extract_qa_pairs.py" \
  "$RUN_DIR/results/B2/qa_records.jsonl" \
  "$RUN_DIR/results/B6/qa_records.jsonl" \
  "$RUN_DIR/results/B7/qa_records.jsonl" \
  "$RUN_DIR/results/all/qa_records.jsonl"

python3 "$RUN_DIR/scripts/extract_qa_by_template.py" \
  "$RUN_DIR/results/B2/qa_records.jsonl" \
  "$RUN_DIR/results/B6/qa_records.jsonl" \
  "$RUN_DIR/results/B7/qa_records.jsonl" \
  --output-dir "$RUN_DIR/results/by_template"
