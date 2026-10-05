#!/bin/zsh
# Freeze or unfreeze long-running jobs (renders, encodes, batch scripts) without losing their progress.
#
#   job_pause.sh pause  PATTERN [PATTERN ...]    # freeze every process whose command line matches a pattern
#   job_pause.sh resume PATTERN [PATTERN ...]    # unfreeze them; they carry on from exactly where they stopped
#   job_pause.sh status PATTERN [PATTERN ...]    # list them: state T = frozen, S/R = running
#
#   e.g.  job_pause.sh pause  'Blender -b' 'my_render_queue\.sh'
#
# Patterns are extended regexes matched against the full command line (pgrep -f). Pause freezes the patterns in
# the order given, so list the queue script BEFORE its workers: a frozen queue cannot start the next job.
# kill -STOP / kill -CONT only: nothing is ever killed, and the processes must belong to you.

[ $# -ge 2 ] || { echo "usage: job_pause.sh pause|resume|status PATTERN [PATTERN ...]"; exit 1; }
action=$1; shift

pids_for() { pgrep -f -- "$1" | grep -v "^$$\$" }

case "$action" in
  pause)
    n=0
    for pat in "$@"; do for p in $(pids_for "$pat"); do kill -STOP $p 2>/dev/null && n=$((n+1)); done; done
    echo "$(date '+%F %T') paused $n process(es)" ;;
  resume)
    n=0
    for pat in "${(Oa)@}"; do for p in $(pids_for "$pat"); do kill -CONT $p 2>/dev/null && n=$((n+1)); done; done
    echo "$(date '+%F %T') resumed $n process(es)" ;;
  status)
    for pat in "$@"; do for p in $(pids_for "$pat"); do ps -o pid=,stat=,etime=,command= -p $p | cut -c1-140; done; done ;;
  *)
    echo "usage: job_pause.sh pause|resume|status PATTERN [PATTERN ...]"; exit 1 ;;
esac
