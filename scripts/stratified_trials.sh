# Reads scripts/stratified_trials.txt, the one list shared with the Windows
# runners. Sourced by the cluster sbatch scripts.
#
# Defines trials_for_task <task>, echoing a comma-separated trial list, empty if
# the task is absent.

STRATIFIED_LIST="${STRATIFIED_LIST:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stratified_trials.txt}"

trials_for_task() {
  local task="$1"
  if [ ! -f "$STRATIFIED_LIST" ]; then
    echo "ERROR: no trial list at $STRATIFIED_LIST" >&2
    return 1
  fi
  # Field 1 must match exactly: Needle_Passing must not be matched by a grep for
  # Suturing, and a substring match would quietly widen any task list.
  awk -v t="$task" '$1 == t {print $2}' "$STRATIFIED_LIST" | paste -sd, -
}
