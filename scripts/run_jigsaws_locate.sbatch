#!/usr/bin/env bash
#SBATCH --job-name=jigsaw-locate
#SBATCH --account=gedas
#SBATCH --partition=a6000
# The run directory and model sit on oprime's own disk. Other a6000 nodes see it as
# /mnt/opr, but sbatch records the working directory as oprime's /playpen-storage
# path, and a job sent elsewhere then fails at once with no log. Pin oprime, or name
# the node and the /mnt/opr path when oprime's GPUs are taken:
#   sbatch --nodelist=megatron.ib --chdir=/mnt/opr/mairuili/surgcoach_runs/<run> scripts/run_jigsaws_locate.sbatch
#SBATCH --nodelist=oprime.ib
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

# The JIGSAWS find-it-in-the-recording questions (generate_event_qa.py --vts), answered
# by Qwen3.6 through llama.cpp on one GPU, as run_jigsaws_enriched_d.sbatch does.
# run_event_qa_jigsaws.sbatch can answer them too, but it loads the full-precision
# model across three free GPUs, which oprime did not have for a whole afternoon on
# 2026-10-21, and generates slower once it does.
#
# Submit from the run directory, which holds scripts/ and Prompts_And_Pipeline/:
#   sbatch scripts/run_jigsaws_locate.sbatch

set -euo pipefail

WORK_DIR="${WORK_DIR:-$PWD}"
JIGSAWS_ROOT="${JIGSAWS_ROOT:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORK_DIR/outputs/locate-${SLURM_JOB_ID:-manual}}"
TASKS_TO_RUN="${TASKS_TO_RUN:-Suturing Needle_Passing Knot_Tying}"
TEMPERATURE="${TEMPERATURE:-0.6}"
MODEL_PATH="${MODEL_PATH:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking/cache/Qwen3.6-35B-A3B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking/tools/llama.cpp/build/bin/llama-server}"
QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
# Jobs sharing a node cannot share a port, so derive it from the job ID.
LLAMA_PORT="${LLAMA_PORT:-$(( 19000 + ${SLURM_JOB_ID:-0} % 1000 ))}"
LLAMA_HOST="http://127.0.0.1:$LLAMA_PORT"

export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/mnt/sun/shared/datasets/surgical_skill/.tmp/locate}"
mkdir -p "$OUTPUT_DIR" "$TMPDIR"
cd "$WORK_DIR"

# No lab node isolates GPUs between jobs, so the GPU Slurm assigns can already be
# full. Use it when it has room, otherwise an idle one, waiting rather than failing.
export CUDA_DEVICE_ORDER=PCI_BUS_ID
NEED_MIB="${NEED_MIB:-24000}"
pick_gpu() {
  local assigned="${CUDA_VISIBLE_DEVICES%%,*}" free
  if [[ -n "$assigned" ]]; then
    free=$(nvidia-smi --id="$assigned" --query-gpu=memory.free --format=csv,noheader,nounits | tr -d ' ')
    if [[ "${free:-0}" -ge "$NEED_MIB" ]]; then echo "$assigned"; return; fi
  fi
  # awk reads to the end: exiting at the first match closes the pipe on nvidia-smi,
  # and under pipefail its SIGPIPE kills the job with no message.
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
echo "using GPU $GPU on $(hostname) (Slurm assigned ${CUDA_VISIBLE_DEVICES:-none})"
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

# One invocation per task, so a failure in one leaves the others' output intact.
# TRIALS (comma separated) runs just those trials instead, to split one set across
# several GPUs. Set it with `export TRIALS=...` and `sbatch --export=ALL`: written as
# --export=ALL,TRIALS=a,b,c, sbatch splits on the commas and passes only the first.
# EXTRA_ARGS goes to the generator as is, e.g. "--min-trial-s 30".
status=0
if [[ -n "${TRIALS:-}" ]]; then runs=(trials); else read -ra runs <<< "$TASKS_TO_RUN"; fi
for run in "${runs[@]}"; do
  if [[ "$run" == trials ]]; then select=(--trials "$TRIALS"); else select=(--task "$run"); fi
  echo "=== $run  $(date '+%H:%M:%S')"
  # shellcheck disable=SC2086  # EXTRA_ARGS is a list of flags
  python3 scripts/generate_event_qa.py --vts \
    --jigsaws-root "$JIGSAWS_ROOT" \
    "${select[@]}" \
    --backend llama --llama-host "$LLAMA_HOST" \
    --temperature "$TEMPERATURE" \
    --max-new-tokens 10000 \
    --max-retries 3 \
    --resume ${EXTRA_ARGS:-} \
    --output-dir "$OUTPUT_DIR/$run" || status=$?
done
echo "=== done  $(date '+%H:%M:%S')"

python3 scripts/dump_locate_qa.py --jigsaws-root "$JIGSAWS_ROOT" \
  --records "$OUTPUT_DIR" \
  -o "$OUTPUT_DIR/jigsaws_locate_full.txt" \
  --md "$OUTPUT_DIR/jigsaws_locate_questions.md" || status=$?
exit "$status"
