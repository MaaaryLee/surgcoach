#!/usr/bin/env bash
#SBATCH --job-name=jigsaw-enriched-d
#SBATCH --account=gedas
#SBATCH --partition=a6000
# The run directory and model sit on oprime's own disk. Other a6000 nodes see it as
# /mnt/opr, but sbatch records the working directory as oprime's /playpen-storage
# path, so job 35865 failed at once on megatron. Pin oprime, or name the node and
# the /mnt/opr path when oprime's GPUs are taken:
#   sbatch --nodelist=megatron.ib --chdir=/mnt/opr/mairuili/surgcoach_runs/<run> scripts/run_jigsaws_enriched_d.sbatch
#SBATCH --nodelist=oprime.ib
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --time=03:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

# Written D1, D2 and D5 questions, one per trial, with the template for each trial
# checked against the Template D ranking strategy. The model judges only from the
# JIGSAWS rubric (Gao et al. 2014, Table 3) and the trial's ratings; gesture and
# kinematic steps go only into the rationale, and verify_jigsaws_step_evidence.py
# recomputes them from the raw files. The default eight trials are run_jigsaws_enriched_d.py's
# DEFAULT_TRIAL_TEMPLATES.
#
# Submit from the run directory, which holds scripts/ and Prompts_And_Pipeline/:
#   sbatch scripts/run_jigsaws_enriched_d.sbatch

set -euo pipefail

WORK_DIR="${WORK_DIR:-$PWD}"
JIGSAWS_ROOT="${JIGSAWS_ROOT:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR/outputs/enriched-d-${SLURM_JOB_ID:-manual}}"
TRIAL_TEMPLATES="${TRIAL_TEMPLATES:-Suturing_C001:D5,Suturing_F002:D5,Suturing_B001:D2,Knot_Tying_D003:D2,Needle_Passing_F003:D2,Suturing_F003:D1,Suturing_B002:D1,Needle_Passing_C003:D1}"
TRIALS="$(echo "$TRIAL_TEMPLATES" | tr ',' '\n' | cut -d: -f1 | paste -sd, -)"
TEMPERATURE="${TEMPERATURE:-0.6}"
MODEL_PATH="${MODEL_PATH:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking/tools/llama.cpp/build/bin/llama-server}"
QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
# Jobs sharing a node cannot share a port, so derive it from the job ID.
LLAMA_PORT="${LLAMA_PORT:-$(( 19000 + ${SLURM_JOB_ID:-0} % 1000 ))}"
LLAMA_HOST="http://127.0.0.1:$LLAMA_PORT"

export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/mnt/sun/shared/datasets/surgical_skill/.tmp/enriched_d}"
mkdir -p "$OUTPUT_DIR" "$TMPDIR"
cd "$WORK_DIR"

python3 scripts/build_jigsaws_enriched_pilot.py \
  --jigsaws-root "$JIGSAWS_ROOT" \
  --trials "$TRIALS" \
  --output-dir "$OUTPUT_DIR"

# Oprime does not isolate GPUs between jobs, so the GPU Slurm assigns can already be
# full: job 35144 could not load the model on a GPU with 13 GB free. Use the
# assigned GPU when it has room, otherwise an idle one, as
# run_annotation_qa_jigsaws_lab.sbatch does, waiting rather than failing.
export CUDA_DEVICE_ORDER=PCI_BUS_ID
NEED_MIB="${NEED_MIB:-24000}"
pick_gpu() {
  local assigned="${CUDA_VISIBLE_DEVICES%%,*}" free
  if [[ -n "$assigned" ]]; then
    free=$(nvidia-smi --id="$assigned" --query-gpu=memory.free --format=csv,noheader,nounits | tr -d ' ')
    if [[ "${free:-0}" -ge "$NEED_MIB" ]]; then echo "$assigned"; return; fi
  fi
  # awk reads to the end: exiting at the first match closed the pipe on nvidia-smi,
  # and under pipefail its SIGPIPE killed job 35145 (exit 141) with no message.
  nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv,noheader,nounits \
    | awk -F', ' -v need="$NEED_MIB" '$2 < 2000 && $3 >= need && pick == "" {pick = $1} END {if (pick != "") print pick}'
}
GPU=""
for attempt in $(seq 1 "${GPU_WAIT_TRIES:-30}"); do
  GPU="$(pick_gpu)"
  [[ -n "$GPU" ]] && break
  echo "poll $attempt: no GPU with ${NEED_MIB} MiB free; waiting 60s"
  sleep 60
done
[[ -n "$GPU" ]] || { echo "ERROR: no GPU with ${NEED_MIB} MiB free after waiting"; exit 1; }
echo "using GPU $GPU (Slurm assigned ${CUDA_VISIBLE_DEVICES:-none})"
export CUDA_VISIBLE_DEVICES="$GPU"

"$LLAMA_BIN" --model "$MODEL_PATH" --no-mmproj --gpu-layers all \
  --ctx-size 32768 --parallel 1 --reasoning-format deepseek --reasoning-preserve \
  --host 127.0.0.1 --port "$LLAMA_PORT" > "$OUTPUT_DIR/llama-server.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 180); do
  curl -fsS "$LLAMA_HOST/health" 2>/dev/null | grep -q '"status":"ok"' && break
  kill -0 "$server_pid" 2>/dev/null || { tail -100 "$OUTPUT_DIR/llama-server.log"; exit 1; }
  sleep 2
done
curl -fsS "$LLAMA_HOST/health" | grep -q '"status":"ok"'

status=0
python3 scripts/run_jigsaws_enriched_d.py \
  --inputs "$OUTPUT_DIR/enriched_trial_inputs.jsonl" \
  --trial-templates "$TRIAL_TEMPLATES" \
  --system-prompt Prompts_And_Pipeline/system-prompt-jigsaws-enriched-d.md \
  --llama-host "$LLAMA_HOST" \
  --temperature "$TEMPERATURE" \
  --max-tokens 10000 \
  --max-retries 5 \
  --output "$OUTPUT_DIR/qa_records.jsonl" ${CALIBRATE_ONLY:+--calibrate-only} || status=$?
[[ -n "${CALIBRATE_ONLY:-}" ]] && exit "$status"

# Recompute every step in the rationales from the raw files with independent code.
python3 scripts/verify_jigsaws_step_evidence.py "$OUTPUT_DIR/qa_records.jsonl" \
  --jigsaws-root "$JIGSAWS_ROOT" \
  --report "$OUTPUT_DIR/step_verification.md" || status=$?
exit "$status"
