#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${RUN_DIR:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-b1345-thinking}"
D75_RUN="${D75_RUN:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking}"
MODEL_PATH="${MODEL_PATH:-$D75_RUN/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-$D75_RUN/tools/llama.cpp/build/bin/llama-server}"
SYSTEM_PROMPT="$RUN_DIR/Prompts_And_Pipeline/system-prompt-A-D.md"
TEMPLATE_QUESTIONS="$RUN_DIR/Prompts_And_Pipeline/template questions.md"
MANIFEST="$RUN_DIR/Prompts_And_Pipeline/b1345-selected-clips.json"

if [ ! -x "$LLAMA_BIN" ]; then
  echo "llama-server is missing or not executable: $LLAMA_BIN" >&2
  exit 1
fi
if [ ! -f "$MODEL_PATH" ]; then
  echo "Model is missing: $MODEL_PATH" >&2
  exit 1
fi

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
    --ctx-size 32768 \
    --parallel 1 \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --reasoning-format deepseek \
    --reasoning-preserve \
    --host 127.0.0.1 \
    --port "$port" \
    > "$server_log" 2>&1 &
  local server_pid=$!
  echo "$server_pid" > "$RUN_DIR/llama-server-$template_id.pid"
  trap 'kill "$server_pid" 2>/dev/null || true' EXIT

  for _ in $(seq 1 180); do
    if curl -fsS "$host/health" >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      echo "llama.cpp server for $template_id exited during startup" >&2
      tail -100 "$server_log" >&2
      exit 1
    fi
    sleep 2
  done
  curl -fsS "$host/health" >/dev/null

  python3 "$RUN_DIR/scripts/run_annotation_qa_b1345.py" \
    --manifest "$MANIFEST" \
    --system-prompt "$SYSTEM_PROMPT" \
    --template-questions "$TEMPLATE_QUESTIONS" \
    --templates "$template_id" \
    --backend real \
    --inference-engine llama \
    --llama-host "$host" \
    --temperature 0.7 \
    --max-new-tokens 7000 \
    --max-retries 3 \
    --output-dir "$output_dir" \
    > "$batch_log" 2>&1
)

run_template_batch B1 0 18201 &
pid_b1=$!
run_template_batch B3 1 18203 &
pid_b3=$!
run_template_batch B4 2 18204 &
pid_b4=$!
run_template_batch B5 3 18205 &
pid_b5=$!

wait "$pid_b1"
wait "$pid_b3"
wait "$pid_b4"
wait "$pid_b5"

python3 "$RUN_DIR/scripts/check_template_b1345.py" \
  "$RUN_DIR/results/B1/qa_records.jsonl" \
  "$RUN_DIR/results/B3/qa_records.jsonl" \
  "$RUN_DIR/results/B4/qa_records.jsonl" \
  "$RUN_DIR/results/B5/qa_records.jsonl" \
  --manifest "$MANIFEST" \
  --template-questions "$TEMPLATE_QUESTIONS" \
  > "$RUN_DIR/logs/check-b1345.log" 2>&1

mkdir -p "$RUN_DIR/results/all"
: > "$RUN_DIR/results/all/qa_records.jsonl"
for template_id in B1 B3 B4 B5; do
  cat "$RUN_DIR/results/$template_id/qa_records.jsonl" \
    >> "$RUN_DIR/results/all/qa_records.jsonl"
done

python3 "$RUN_DIR/scripts/extract_qa_pairs.py" \
  "$RUN_DIR/results/B1/qa_records.jsonl" \
  "$RUN_DIR/results/B3/qa_records.jsonl" \
  "$RUN_DIR/results/B4/qa_records.jsonl" \
  "$RUN_DIR/results/B5/qa_records.jsonl" \
  "$RUN_DIR/results/all/qa_records.jsonl"

python3 "$RUN_DIR/scripts/extract_qa_by_template.py" \
  "$RUN_DIR/results/B1/qa_records.jsonl" \
  "$RUN_DIR/results/B3/qa_records.jsonl" \
  "$RUN_DIR/results/B4/qa_records.jsonl" \
  "$RUN_DIR/results/B5/qa_records.jsonl" \
  --output-dir "$RUN_DIR/results/by_template"
