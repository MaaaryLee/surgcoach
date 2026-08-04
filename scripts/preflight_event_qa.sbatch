#!/usr/bin/env bash
#SBATCH --job-name=preflight-event-qa
# Same partition as the GPU job, because that name is known to be accepted here,
# but no --gres: this check loads no model and needs no GPU.
#SBATCH --partition=a6000
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=preflight-event-qa-%j.out
#SBATCH --error=preflight-event-qa-%j.out

# Answers, from a compute node, the four things that have to be true before the
# event-QA job is worth submitting. It asks for no GPU and loads no model, so it
# costs a couple of minutes and queues immediately.
#
# It exists because the last cluster attempt died on "couldn't chdir to
# /playpen-storage/paneric/surgcoach", and the two causes guessed from the login
# node were both wrong: the walltime cap (the partition is `infinite`) and a
# missing pull (HEAD was already current). chdir happens before any script line
# runs, so only a job that reaches a compute node can report on it.
#
#   sbatch scripts/preflight_event_qa.sbatch
#   cat preflight-event-qa-*.out
#
# Every check prints OK or FAIL with the value it saw. It does not exit early:
# one run should report every problem, not the first.

echo "=== 1. where this job landed ==="
echo "hostname:          $(hostname)"
echo "SLURM_SUBMIT_DIR:  ${SLURM_SUBMIT_DIR:-<unset>}"
echo "PWD:               $PWD"
echo "whoami:            $(whoami)"

fails=0
check() {  # check <label> <ok?> <detail>
  if [ "$2" = "0" ]; then echo "OK    $1: $3"; else echo "FAIL  $1: $3"; fails=$((fails + 1)); fi
}

WORK_DIR="${WORK_DIR:-$PWD}"
[ -f "$WORK_DIR/scripts/generate_event_qa.py" ]
check "repo visible from this node" $? "$WORK_DIR"

# The failure mode that killed the last attempt: the submit directory exists on
# the login node but is not mounted on compute nodes, or is read-only there.
touch "$WORK_DIR/.preflight_write_test" 2>/dev/null
w=$?
rm -f "$WORK_DIR/.preflight_write_test" 2>/dev/null
check "work dir writable from this node" $w "$WORK_DIR"

echo
echo "=== 2. dataset: does it have kinematics, not just transcriptions? ==="
# The event pipeline needs a third annotation source the C/D pipeline never read.
# meta_file + transcriptions are enough for C/D; localize_events also reads
# kinematics/, and a root missing it produces zero events with no error.
JIGSAWS_ROOT="${JIGSAWS_ROOT:-/mnt/sun/shared/datasets/surgical_skill/JIGSAWS}"
echo "JIGSAWS_ROOT: $JIGSAWS_ROOT"
for task in Suturing Knot_Tying Needle_Passing; do
  meta=$(find "$JIGSAWS_ROOT/$task" -name "meta_file_${task}.txt" -print -quit 2>/dev/null || true)
  if [ -z "$meta" ]; then
    check "$task" 1 "no meta_file_${task}.txt under $JIGSAWS_ROOT/$task"
    continue
  fi
  root=$(dirname "$meta")
  n_trials=$(awk 'NF >= 9' "$meta" | grep -c . || true)
  n_trans=$(ls "$root/transcriptions"/*.txt 2>/dev/null | grep -c . || true)
  n_kin=$(find "$root/kinematics" -name '*.txt' 2>/dev/null | grep -c . || true)
  [ "$n_kin" -gt 0 ]
  check "$task" $? "root=$root  meta=$n_trials  transcriptions=$n_trans  kinematics=$n_kin"
done

echo
echo "=== 3. python: numpy, and torch if the GPU job is next ==="
QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-/mnt/sun/shared/datasets/surgical_skill/.python/qwen36/site-packages}"
export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"
echo "python3: $(command -v python3)  $(python3 --version 2>&1)"
python3 -c 'import numpy; print("numpy", numpy.__version__)'
check "numpy imports" $? "needed by localize_events for the kinematics arrays"
python3 -c 'import transformers, torch; print("transformers", transformers.__version__, "torch", torch.__version__)'
check "transformers+torch import" $? "from $QWEN36_PACKAGE_DIR"

echo
echo "=== 4. the pipeline itself, on cluster data, with no model ==="
# --questions-only exercises every annotation reader and every detector and
# prints the questions it would ask. If this works, the GPU job's only remaining
# unknown is the model load.
cd "$WORK_DIR" || { echo "FAIL  cannot cd to $WORK_DIR"; exit 1; }
suturing_root=$(dirname "$(find "$JIGSAWS_ROOT/Suturing" -name meta_file_Suturing.txt -print -quit 2>/dev/null || true)")
if [ -n "$suturing_root" ] && [ "$suturing_root" != "." ]; then
  python3 scripts/generate_event_qa.py \
    --jigsaws-root "$JIGSAWS_ROOT" \
    --trials Suturing_C004,Suturing_D004 \
    --questions-only
  check "question builder runs on cluster data" $? "two trials, no model"
else
  check "question builder runs on cluster data" 1 "skipped: no Suturing root"
fi

echo
echo "======================================================================"
if [ "$fails" -eq 0 ]; then
  echo "ALL CHECKS PASSED -- submit scripts/run_event_qa_jigsaws.sbatch from $WORK_DIR"
else
  echo "$fails CHECK(S) FAILED -- fix these before submitting the GPU job"
fi
exit "$fails"
