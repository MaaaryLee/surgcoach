# Sourced by preflight_event_qa.sbatch and run_event_qa_jigsaws.sbatch.
#
# The two clusters this project uses share a dataset mount and a prebuilt
# site-packages directory but differ in three ways that have each cost a failed
# job: where the extracted Python headers live, whether the HF cache is
# per-user, and whether WORK_DIR is the submit directory or a fixed absolute
# path. Detecting those beats maintaining a near-duplicate script per cluster,
# where a fix lands in one copy and not the other.
#
# Every value stays overridable, so an unforeseen third cluster needs env vars
# rather than an edit:
#
#   WORK_DIR=/home/$USER/annotation_qa sbatch scripts/preflight_event_qa.sbatch
#
# Sets: WORK_DIR SHARED JIGSAWS_ROOT QWEN36_PACKAGE_DIR SYSTEM_PROMPT
#       PYTHONPATH HF_HOME TMPDIR CPATH, and CLUSTER_NOTES (lines to print).

CLUSTER_NOTES=""
note() { CLUSTER_NOTES="${CLUSTER_NOTES}${1}"$'\n'; }

# SLURM_SUBMIT_DIR when submitted, $PWD when run by hand. On the cluster whose
# repo sits at a fixed absolute path, pass WORK_DIR explicitly.
WORK_DIR="${WORK_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}"
WORK_DIR="$(cd "$WORK_DIR" 2>/dev/null && pwd || echo "$WORK_DIR")"
note "WORK_DIR            $WORK_DIR"

SHARED="${SHARED:-/mnt/sun/shared/datasets/surgical_skill}"
JIGSAWS_ROOT="${JIGSAWS_ROOT:-$SHARED/JIGSAWS}"
QWEN36_PACKAGE_DIR="${QWEN36_PACKAGE_DIR:-$SHARED/.python/qwen36/site-packages}"
note "JIGSAWS_ROOT        $JIGSAWS_ROOT"
note "QWEN36_PACKAGE_DIR  $QWEN36_PACKAGE_DIR"

# The prompt sits under Prompts_And_Pipeline/ in the repo, but one cluster's work
# dir holds the prompts flat at the top level, copied in rather than cloned.
if [ -z "${SYSTEM_PROMPT:-}" ]; then
  for cand in "$WORK_DIR/Prompts_And_Pipeline/system-prompt-events.md" \
              "$WORK_DIR/system-prompt-events.md"; do
    if [ -f "$cand" ]; then SYSTEM_PROMPT="$cand"; break; fi
  done
fi
note "SYSTEM_PROMPT       ${SYSTEM_PROMPT:-<not found>}"

export PYTHONPATH="$QWEN36_PACKAGE_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Triton (inside Qwen3.6's MoE code) JIT-compiles a C helper and needs Python.h,
# which neither cluster provides system-wide. Both extracted it without root, to
# different places. Probe for the directory that actually contains python3.10/
# rather than assuming either.
#
#   cd <somewhere writable> && mkdir -p pydev && cd pydev \
#     && apt download libpython3.10-dev && dpkg -x libpython3.10-dev*.deb .
HEADER_ROOT=""
for cand in "${PYDEV_ROOT:-}" \
            "$SHARED/.tmp/python_headers/libroot/usr/include" \
            "$(dirname "$WORK_DIR")/pydev/usr/include" \
            "$HOME/pydev/usr/include"; do
  if [ -n "$cand" ] && [ -d "$cand/python3.10" ]; then HEADER_ROOT="$cand"; break; fi
done
if [ -n "$HEADER_ROOT" ]; then
  export CPATH="$HEADER_ROOT/python3.10:$HEADER_ROOT/x86_64-linux-gnu/python3.10:$HEADER_ROOT${CPATH:+:$CPATH}"
fi
note "PYTHON HEADERS      ${HEADER_ROOT:-<not found>}"

# A name for this user, for per-user cache and scratch paths. $USER is unset in a
# batch step and `whoami` fails outright on these compute nodes -- they cannot
# resolve the UID against any name service, so it returns empty. An empty name
# silently built ".../huggingface/" with nothing on the end, so fall back to the
# numeric UID, which always resolves.
WHO="${USER:-$(id -un 2>/dev/null || true)}"
WHO="${WHO:-uid$(id -u)}"
note "USER                $WHO"

# Per-user, because the HF cache is on a shared mount and two users writing the
# same snapshot directory is how a half-downloaded model gets read as complete.
# Falls back beside the work dir if the shared cache is not writable.
#
# Watch this line. The 35B snapshot is ~69 GB, so an HF_HOME that does not
# already hold it means downloading it before generation starts.
if [ -z "${HF_HOME:-}" ]; then
  cand="$SHARED/.cache/huggingface/$WHO"
  if mkdir -p "$cand" 2>/dev/null; then HF_HOME="$cand"
  else HF_HOME="$(dirname "$WORK_DIR")/.cache/huggingface"; fi
fi
export HF_HOME
# SLURM presets TMPDIR to node-local /tmp, which is correct for scratch and
# faster than the shared mount, so it is left alone when already set.
export TMPDIR="${TMPDIR:-$SHARED/.tmp/event_qa_$WHO}"
note "HF_HOME             $HF_HOME"
note "TMPDIR              $TMPDIR"
if [ -d "$HF_HOME/hub" ] || [ -d "$HF_HOME/models--Qwen--Qwen3.6-35B-A3B" ]; then
  note "HF_HOME state       has a hub/ cache"
else
  note "HF_HOME state       EMPTY -- expect a ~69 GB download before generation"
fi

export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export PYTHONFAULTHANDLER=1
