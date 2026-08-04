#!/usr/bin/env bash
#SBATCH --job-name=preflight-event-qa
# Same partition as the GPU job, because that name is known to be accepted on
# both clusters, but no --gres: this check loads no model and needs no GPU.
#SBATCH --partition=a6000
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
# Relative, so it lands in the submit directory. The other sbatch scripts here
# use absolute log paths under a work dir, which fails with no log at all when
# that directory does not exist -- #SBATCH lines are read before any mkdir in
# the script body can run.
#SBATCH --output=preflight-event-qa-%j.out
#SBATCH --error=preflight-event-qa-%j.out

# Answers, from a compute node, everything that has to be true before the
# event-QA GPU job is worth submitting. No GPU, no model: a couple of minutes.
#
# It exists because the last cluster attempt died on "couldn't chdir to
# <work dir>", and both causes guessed from the login node were wrong: the
# walltime cap (the partition is `infinite`) and a missing pull (HEAD was
# already current). chdir happens before any script line runs, so only a job
# that reaches a compute node can report on it.
#
#   sbatch scripts/preflight_event_qa.sbatch
#   cat preflight-event-qa-*.out
#
# If the repo lives at a fixed path rather than where you submit from:
#   WORK_DIR=/home/$USER/annotation_qa sbatch scripts/preflight_event_qa.sbatch
#
# Every check prints OK or FAIL with the value it saw, and none exits early: one
# run should report every problem, not just the first.

echo "=== 1. where this job landed ==="
echo "hostname:          $(hostname)"
echo "SLURM_SUBMIT_DIR:  ${SLURM_SUBMIT_DIR:-<unset>}"
echo "PWD:               $PWD"
echo "whoami:            $(whoami)"

fails=0
check() {  # check <label> <status> <detail>
  if [ "$2" = "0" ]; then echo "OK    $1: $3"; else echo "FAIL  $1: $3"; fails=$((fails + 1)); fi
}

# Deliberately not `set -e`: a failing check must be recorded and the run
# continue, which is the entire point of this script.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HERE/cluster_env_event_qa.sh" ]; then
  # shellcheck source=cluster_env_event_qa.sh
  . "$HERE/cluster_env_event_qa.sh"
elif [ -f "${SLURM_SUBMIT_DIR:-$PWD}/scripts/cluster_env_event_qa.sh" ]; then
  . "${SLURM_SUBMIT_DIR:-$PWD}/scripts/cluster_env_event_qa.sh"
else
  echo "FAIL  cannot find scripts/cluster_env_event_qa.sh -- copy it across too" >&2
  exit 1
fi

echo
echo "=== 2. resolved cluster paths ==="
printf '%s' "$CLUSTER_NOTES"

echo
echo "=== 3. work dir, from this node ==="
[ -f "$WORK_DIR/scripts/generate_event_qa.py" ]
check "repo visible from this node" $? "$WORK_DIR"
[ -f "$WORK_DIR/scripts/localize_events.py" ]
check "localize_events.py present" $? "the detectors; new on this cluster"
[ -n "${SYSTEM_PROMPT:-}" ] && [ -f "$SYSTEM_PROMPT" ]
check "events system prompt present" $? "${SYSTEM_PROMPT:-<not found>}"

# The work dir is a clone, and the copied-in generator imports four symbols from
# the runner already there. An older clone can be missing them, which fails at
# import time -- after the copy looked fine.
echo "clone HEAD: $(cd "$WORK_DIR" && git log --oneline -1 2>/dev/null || echo '<not a git clone>')"
missing=""
for sym in TRANSPORT_RETRIES TRANSPORT_BACKOFF_SECONDS load_text_model \
           run_generation extract_system_prompt parse_json_payload; do
  grep -q "$sym" "$WORK_DIR/scripts/run_annotation_qa_jigsaws.py" 2>/dev/null || missing="$missing $sym"
done
[ -z "$missing" ]
check "runner exposes what the generator imports" $? "${missing:-all six present}"

# The failure that killed the last attempt: the work dir exists on the login node
# but is not mounted on compute nodes, or is read-only there.
touch "$WORK_DIR/.preflight_write_test" 2>/dev/null
w=$?
rm -f "$WORK_DIR/.preflight_write_test" 2>/dev/null
check "work dir writable from this node" $w "$WORK_DIR"

mkdir -p "$TMPDIR" 2>/dev/null
check "TMPDIR creatable" $? "$TMPDIR"
mkdir -p "$HF_HOME" 2>/dev/null
check "HF_HOME creatable" $? "$HF_HOME"
[ -n "${HEADER_ROOT:-}" ]
check "Python.h found" $? "${HEADER_ROOT:-<none>} -- Triton needs it for Qwen3.6's MoE kernels"

echo
echo "=== 4. dataset: kinematics, not just transcriptions ==="
# The event pipeline reads a third annotation source the C/D pipeline never did.
# meta_file + transcriptions suffice for C/D; localize_events also reads
# kinematics/, and a root missing it produces zero events with no error at all.
for task in Suturing Knot_Tying Needle_Passing; do
  meta=$(find "$JIGSAWS_ROOT/$task" -name "meta_file_${task}.txt" -print -quit 2>/dev/null)
  if [ -z "$meta" ]; then
    check "$task" 1 "no meta_file_${task}.txt under $JIGSAWS_ROOT/$task"
    continue
  fi
  root=$(dirname "$meta")
  n_trials=$(awk 'NF >= 9' "$meta" | grep -c . || true)
  n_trans=$(find "$root/transcriptions" -name '*.txt' 2>/dev/null | grep -c . || true)
  n_kin=$(find "$root/kinematics" -name '*.txt' 2>/dev/null | grep -c . || true)
  [ "$n_kin" -gt 0 ]
  check "$task" $? "root=$root  meta=$n_trials  transcriptions=$n_trans  kinematics=$n_kin"
done

echo
echo "=== 5. python: numpy, and torch for the GPU job ==="
echo "python3: $(command -v python3)  $(python3 --version 2>&1)"
python3 -c 'import numpy; print("numpy", numpy.__version__)'
check "numpy imports" $? "localize_events needs it for the kinematics arrays"
python3 -c 'import transformers, torch; print("transformers", transformers.__version__, "torch", torch.__version__)'
check "transformers+torch import" $? "from $QWEN36_PACKAGE_DIR"

echo
echo "=== 6. the pipeline itself, on cluster data, with no model ==="
# --questions-only exercises every annotation reader and every detector, then
# prints the questions it would ask. If this passes, the GPU job's only
# remaining unknown is the model load.
if cd "$WORK_DIR"; then
  python3 scripts/generate_event_qa.py \
    --jigsaws-root "$JIGSAWS_ROOT" \
    --trials Suturing_C004,Knot_Tying_G004 \
    --questions-only
  check "question builder runs on cluster data" $? "two trials, no model"
else
  check "question builder runs on cluster data" 1 "could not cd to $WORK_DIR"
fi

echo
echo "======================================================================"
if [ "$fails" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
  echo "submit: WORK_DIR=$WORK_DIR sbatch scripts/run_event_qa_jigsaws.sbatch"
else
  echo "$fails CHECK(S) FAILED -- fix these before submitting the GPU job"
fi
exit "$fails"
