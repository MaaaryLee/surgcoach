#!/bin/bash
#SBATCH --account=gedas
#SBATCH --partition=a6000
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --job-name=zs-video
#SBATCH --output=logs/zs-video-%j.out
# Zero-shot video QA for one model, or the judge pass over every model's answers.
#
#   MODEL=qwen38-27b  sbatch --chdir=<run dir> scripts/zs_video/zs_video.sbatch
#   MODEL=qwen3vl-8b  sbatch --chdir=<run dir> scripts/zs_video/zs_video.sbatch
#   MODEL=judge       sbatch --chdir=<run dir> scripts/zs_video/zs_video.sbatch
#   MODEL=judge-oss   sbatch --gres=gpu:2 --chdir=<run dir> scripts/zs_video/zs_video.sbatch
#
# Each preset below is the one setting tested for that model (Yulu, 11-17: one best setting per
# model). The frames are the same for every model: video_units/ from prep_video_units.py.
# LIMIT=N runs only the first N questions of each file (smoke test).
set -euo pipefail
WORK_DIR="${WORK_DIR:-$PWD}"
cd "$WORK_DIR"
LLAMA_BIN="${LLAMA_BIN:-/mnt/opr/mairuili/surgcoach_runs/2026-07-29-d75-thinking/tools/llama.cpp/build/bin/llama-server}"
M="${MODELS_DIR:-/mnt/opr/mairuili/models}"
mkdir -p logs
LLAMA_PORT="${LLAMA_PORT:-$(( 19000 + ${SLURM_JOB_ID:-0} % 1000 ))}"
HOST="http://127.0.0.1:$LLAMA_PORT"
UNITS="${UNITS:-$(ls video_units/*.jsonl | tr '\n' ' ')}"
LIMIT_ARG=${LIMIT:+--limit $LIMIT}

# ---------------------------------------------------------------- presets
# MODEL_ARGS: the GGUF files llama-server loads (--mmproj adds the vision encoder).
case "${MODEL:?set MODEL}" in
  qwen38-27b)
    MODEL_ID="Qwen3.8-27B Q4_K_M + mmproj BF16 (llama.cpp)"
    MODEL_ARGS="${MODEL_ARGS:---model $M/Qwen3.8-27B-Q4_K_M.gguf --mmproj $M/mmproj-Qwen3.8-27B-BF16.gguf}"
    # Qwen3 thinking-mode sampling, as in the 11-09 text-only run
    GEN_ARGS="--thinking --temperature 0.6 --top-p 0.95 --top-k 20 --max-tokens 8000"
    # two slots used 24 GB of 48 on 11-17, so four fit
    PARALLEL="${PARALLEL:-4}"; CTX_PER_SLOT="${CTX_PER_SLOT:-32768}"; NEED_MIB="${NEED_MIB:-40000}" ;;
  qwen3vl-8b)
    MODEL_ID="Qwen3-VL-8B-Instruct Q8_0 + mmproj F16 (llama.cpp)"
    MODEL_ARGS="${MODEL_ARGS:---model $M/Qwen3VL-8B-Instruct-Q8_0.gguf --mmproj $M/mmproj-Qwen3VL-8B-Instruct-F16.gguf}"
    # Qwen3-VL Instruct's recommended vision-language sampling
    GEN_ARGS="--temperature 0.7 --top-p 0.8 --top-k 20 --presence-penalty 1.5 --max-tokens 2048"
    PARALLEL="${PARALLEL:-4}"; CTX_PER_SLOT="${CTX_PER_SLOT:-24576}"; NEED_MIB="${NEED_MIB:-24000}" ;;
  judge)
    JUDGE_ID="${JUDGE_ID:-gemma-3-27b-it Q4_K_M}"
    MODEL_ARGS="${MODEL_ARGS:---model $M/gemma-3-27b-it-Q4_K_M.gguf}"   # text only: no --mmproj
    PARALLEL="${PARALLEL:-4}"; CTX_PER_SLOT="${CTX_PER_SLOT:-16384}"; NEED_MIB="${NEED_MIB:-30000}" ;;
  judge-oss)
    # The stronger judge (11-17): 63 GB of weights, so two GPUs. Submit with --gres=gpu:2.
    JUDGE_ID="${JUDGE_ID:-gpt-oss-120b MXFP4}"
    MODEL_ARGS="${MODEL_ARGS:---model $M/gpt-oss-120b-MXFP4.gguf}"
    # OpenAI's recommended sampling for gpt-oss, and its default reasoning effort
    JUDGE_ARGS="--judge-temperature 1.0 --judge-top-p 1.0 --reasoning-effort medium"
    NGPU=2; PARALLEL="${PARALLEL:-8}"; CTX_PER_SLOT="${CTX_PER_SLOT:-16384}"; NEED_MIB="${NEED_MIB:-40000}" ;;
  *) echo "unknown MODEL $MODEL"; exit 1 ;;
esac
# PART names one share of the questions when a model is split over several jobs (UNITS lists them).
# CLAIM_DIR instead lets several jobs draw from one list: give each job the same UNITS and CLAIM_DIR,
# and each question goes to whichever job reaches it first. Each job writes outputs/<model>/pool-<job id>
# and skips questions any other folder of that model has already answered.
if [[ -n "${CLAIM_DIR:-}" && "$MODEL" != judge* ]]; then
  OUT_DIR="${OUT_DIR:-outputs/$MODEL/pool-${SLURM_JOB_ID:-manual}}"
fi
OUT_DIR="${OUT_DIR:-outputs/$MODEL${PART:+/part-$PART}}"

# ---------------------------------------------------------------- GPU
export CUDA_DEVICE_ORDER=PCI_BUS_ID
pick_gpu() {
  local assigned="${CUDA_VISIBLE_DEVICES%%,*}" free
  if [[ -n "$assigned" ]]; then
    free=$(nvidia-smi --id="$assigned" --query-gpu=memory.free --format=csv,noheader,nounits | tr -d ' ')
    if [[ "${free:-0}" -ge "$NEED_MIB" ]]; then echo "$assigned"; return; fi
    # The GPU Slurm gave us can still hold the last job's memory for a moment. Wait for it
    # (about 5 min) before looking elsewhere: on 11-17 two judge jobs that started together
    # both fell back to the same idle GPU and the second ran out of memory.
    if [[ "${PICK_ATTEMPT:-1}" -le 10 ]]; then return; fi
  fi
  nvidia-smi --query-gpu=index,memory.used,memory.free --format=csv,noheader,nounits \
    | awk -F', ' -v need="$NEED_MIB" '$2 < 2000 && $3 >= need && pick == "" {pick = $1} END {if (pick != "") print pick}'
}
GPU=""
if [[ "${NGPU:-1}" -gt 1 ]]; then
  # several GPUs: the ones Slurm assigned, once each has room
  IFS=, read -ra ASSIGNED <<< "${CUDA_VISIBLE_DEVICES:-}"
  [[ "${#ASSIGNED[@]}" -ge "$NGPU" ]] || { echo "ERROR: need $NGPU GPUs, got '${CUDA_VISIBLE_DEVICES:-}' (submit with --gres=gpu:$NGPU)"; exit 1; }
  for attempt in $(seq 1 20); do
    ok=1
    for g in "${ASSIGNED[@]:0:$NGPU}"; do
      free=$(nvidia-smi --id="$g" --query-gpu=memory.free --format=csv,noheader,nounits | tr -d ' ')
      [[ "${free:-0}" -ge "$NEED_MIB" ]] || ok=0
    done
    [[ "$ok" -eq 1 ]] && break; echo "poll $attempt: assigned GPUs not free yet"; sleep 30
  done
  [[ "$ok" -eq 1 ]] || { echo "ERROR: assigned GPUs never freed"; exit 1; }
  GPU="$(IFS=,; echo "${ASSIGNED[*]:0:$NGPU}")"
else
  for attempt in $(seq 1 40); do GPU="$(PICK_ATTEMPT=$attempt pick_gpu)"; [[ -n "$GPU" ]] && break; echo "poll $attempt: no free GPU"; sleep 30; done
fi
[[ -n "$GPU" ]] || { echo "ERROR: no GPU free"; exit 1; }
export CUDA_VISIBLE_DEVICES="$GPU"
echo "node $(hostname) GPU $GPU model $MODEL ($MODEL_ARGS) parallel $PARALLEL ctx/slot $CTX_PER_SLOT"

# ---------------------------------------------------------------- server
mkdir -p "$OUT_DIR"
# shellcheck disable=SC2086
"$LLAMA_BIN" $MODEL_ARGS --gpu-layers all --ctx-size $(( PARALLEL * CTX_PER_SLOT )) \
  --parallel "$PARALLEL" --batch-size 4096 --ubatch-size 2048 --reasoning-format deepseek \
  --host 127.0.0.1 --port "$LLAMA_PORT" > "$OUT_DIR/llama-server-${SLURM_JOB_ID:-manual}.log" 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 600); do
  curl -fsS "$HOST/health" 2>/dev/null | grep -q '"status":"ok"' && break
  kill -0 "$server_pid" 2>/dev/null || { tail -60 "$OUT_DIR/llama-server-${SLURM_JOB_ID:-manual}.log"; exit 1; }
  sleep 2
done
curl -fsS "$HOST/health" | grep -q '"status":"ok"' || { echo "server never came up"; exit 1; }

# ---------------------------------------------------------------- run
if [[ "$MODEL" == judge* ]]; then
  if [[ -n "${CHECK:-}" ]]; then
    # first score the judge on answers whose right label is known (judge_check.py), then judge all
    # shellcheck disable=SC2086
    python3 -I scripts/zs_video/judge_check.py --check "$CHECK" --units $UNITS --host "$HOST" \
      --workers "$PARALLEL" --judge-id "$JUDGE_ID" --max-tokens 4000 ${JUDGE_ARGS:-} \
      ${CHECK_COMPARE:+--compare "$CHECK_COMPARE"} --out "$OUT_DIR/judge_check-${SLURM_JOB_ID:-manual}.jsonl" || true
  fi
  ANSWERS="${ANSWERS:-$(find outputs -name generations.jsonl -not -path 'outputs/judge*' | sort | tr '\n' ' ')}"
  # shellcheck disable=SC2086
  python3 -I scripts/zs_video/zs_video_run.py judge --units $UNITS --answers $ANSWERS \
    --host "$HOST" --workers "$PARALLEL" --judge-id "$JUDGE_ID" --max-tokens 4000 $LIMIT_ARG ${JUDGE_ARGS:-}
else
  POOL_ARGS=""
  if [[ -n "${CLAIM_DIR:-}" ]]; then
    POOL_ARGS="--claim-dir $CLAIM_DIR --answered-elsewhere $(find "outputs/$MODEL" -name generations.jsonl | sort | tr '\n' ' ')"
  fi
  # shellcheck disable=SC2086
  python3 -I scripts/zs_video/zs_video_run.py generate --units $UNITS --out "$OUT_DIR" \
    --host "$HOST" --workers "$PARALLEL" --model-id "$MODEL_ID" $GEN_ARGS $LIMIT_ARG $POOL_ARGS
fi
