#!/usr/bin/env bash
# Builds the full QA set on oprime (see build_full_qa.py). Run from the run folder, which holds
# the 11-09 run's scripts/ and Prompts_And_Pipeline/, plus scripts/zs_video/prep_video_units.py
# from the 11-17 run and scripts/full_qa/. The four build steps run side by side.
set -euo pipefail
mkdir -p logs
pids=()
for step in b mb140-d jigsaws-d vts; do
  python3 -I scripts/full_qa/build_full_qa.py "$step" > "logs/$step.log" 2>&1 &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "$p"; done
python3 -I scripts/full_qa/build_full_qa.py finish 2>&1 | tee logs/finish.log
