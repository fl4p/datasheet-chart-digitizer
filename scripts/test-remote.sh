#!/usr/bin/env bash
# Run pytest for this worktree on a remote Linux host (default vup.fabi.me), as if it ran locally.
#
#   scripts/test-remote.sh [pytest args...]     full suite (-m ""), up to 12 workers; args appended
#   scripts/test-remote.sh -m "not slow"        fast subset
#   scripts/test-remote.sh tests/test_x.py -k y
#
# Syncs the worktree to the same absolute path on the host, keeps a Linux venv keyed on
# sha256(pyproject.toml + uv.lock) and the corpus subset the tests read (scripts/remote_corpus.py),
# streams pytest's output and exits with its code. The job runs detached on the host (log and exit
# code in ~/.cache/dsdig-test/runs/<id>/), so a dropped connection does not kill it; Ctrl-C or
# SIGTERM here stops it, and a host-side watchdog stops it when this script stops polling for
# DSDIG_REMOTE_HB_TIMEOUT s (600; e.g. after kill -9). DSDIG_REMOTE_POLL: poll interval (5 s).
# pytest runs inside `offload hold` (fair-share slot queue): it waits for slots, starts with at least
# DSDIG_REMOTE_MIN of up to DSDIG_REMOTE_N, and uses -n = the slots it got.
#
# Env: DSDIG_REMOTE_HOST (vup.fabi.me), DSDIG_REMOTE_N (12), DSDIG_REMOTE_MIN (1; set it to N for
# timing runs), DSDIG_REMOTE_GATHER (30 s), DSDIG_REMOTE_TAG (else $OFFLOAD_TAG, else dsdig-tests),
# DSDIG_REMOTE_NO_CORPUS=1 (skip the
# corpus sync), DSDIG_REMOTE_PY (3.11, the Python of the remote venv), DSDIG_REMOTE_CACHE (name of the
# remote cache dir under ~/.cache/dsdig-test, default test-cache). Passed through when set:
# DSDIG_TEST_CACHE, DSDIG_PROFILE_SUBPROCESS, DSDIG_TEST_CACHE_STATS (remote paths).
#
# Host prerequisites (one-time, user-space): uv in ~/.local/bin; tesseract 5.5.3 + poppler + libxcb +
# libgl in ~/opt/dsdig-tools (micromamba, conda-forge); opencv-python's X11/GL libraries symlinked into
# ~/opt/dsdig-tools/xlib (LD_LIBRARY_PATH, so conda's other libs never shadow the system ones). Paths under /Users/ go to /Users on the host when it
# exists and is writable, else to ~/mirror/Users, with pytest run under proot binding it to /Users.
set -euo pipefail

# whole script in a function: bash parses it before running, so editing it mid-run is safe
main() {

HOST=${DSDIG_REMOTE_HOST:-vup.fabi.me}
NPROC=${DSDIG_REMOTE_N:-12}
MINSLOTS=${DSDIG_REMOTE_MIN:-1}
GATHER=${DSDIG_REMOTE_GATHER:-30}
TAG=${DSDIG_REMOTE_TAG:-${OFFLOAD_TAG:-dsdig-tests}}
OFFLOAD=${OFFLOAD:-$HOME/bin/offload}
command -v timeout >/dev/null || { echo "test-remote: needs coreutils timeout (brew install coreutils)" >&2; return 2; }
PYVER=${DSDIG_REMOTE_PY:-3.11}
TREE=$(cd "$(dirname "$0")/.." && pwd -L)
case $TREE in /private/tmp/*) TREE=${TREE#/private} ;; esac
DATASHEET_ROOT=/Users/fab/dev/pv/pwr-mosfet-lib
VENDORPULL_ROOT=/Users/fab/dev/pv/ee/dcdc-tools
# no connection sharing: a wedged ControlMaster stalled whole runs (2026-11-25)
SSH=(ssh -o BatchMode=yes -o ServerAliveInterval=30 -o ConnectTimeout=30 -o ControlPath=none)

info=$(timeout 120 "${SSH[@]}" "$HOST" 'echo "$HOME"; if [ -d /Users/fab ] && [ -w /Users/fab ]; then echo direct; else echo proot; fi')
RHOME=$(sed -n 1p <<<"$info")
MODE=$(sed -n 2p <<<"$info")
TOOLS=$RHOME/opt/dsdig-tools
STATE=$RHOME/.cache/dsdig-test
remote_path() {  # local absolute path -> where it lives on the host's filesystem
  if [[ $MODE == proot && $1 == /Users/* ]]; then echo "$RHOME/mirror$1"; else echo "$1"; fi
}

RTREE=$(remote_path "$TREE")
"${SSH[@]}" "$HOST" "mkdir -p '$RTREE' '$STATE'"
rsync -e "${SSH[*]}" --timeout=300 -a --delete --exclude .git --exclude /out/ --exclude __pycache__/ --exclude .venv/ \
  --exclude '*.egg-info/' --exclude .pytest_cache/ "$TREE/" "$HOST:$RTREE/"
# git metadata (provenance stamps read `git rev-parse HEAD` / `git status`): the worktree's .git
# file or dir, and the common git dir it points to, at their mirrored paths
GITCOMMON=$(git -C "$TREE" rev-parse --path-format=absolute --git-common-dir)
case $GITCOMMON in /private/tmp/*) GITCOMMON=${GITCOMMON#/private} ;; esac
if [[ $GITCOMMON != "$TREE/.git" ]]; then
  "${SSH[@]}" "$HOST" "mkdir -p '$(remote_path "$GITCOMMON")'"
  rsync -e "${SSH[*]}" --timeout=300 -a --delete "$GITCOMMON/" "$HOST:$(remote_path "$GITCOMMON")/"
  rsync -e "${SSH[*]}" --timeout=300 -a "$TREE/.git" "$HOST:$RTREE/.git"
else
  rsync -e "${SSH[*]}" --timeout=300 -a --delete "$TREE/.git/" "$HOST:$RTREE/.git/"
fi

if [[ ${DSDIG_REMOTE_NO_CORPUS:-0} != 1 ]]; then
  list=$(mktemp -t dsdig-corpus.XXXXXX)
  trap 'rm -f "$list"' EXIT
  python3 "$TREE/scripts/remote_corpus.py" | sed 's#^/##' >"$list"
  dest=/
  [[ $MODE == proot ]] && dest=$RHOME/mirror/
  # every listed path is under /Users (corpus, vendorpull store); --files-from keeps them relative
  rsync -e "${SSH[*]}" --timeout=300 -a --files-from="$list" / "$HOST:$dest"
fi

KEY=$(cat "$TREE/pyproject.toml" "$TREE/uv.lock" | shasum -a 256 | cut -c1-16)
VENV=$STATE/venv-$KEY
VPVENV=$STATE/vendorpull-venv
# build once per KEY (flock: concurrent worktrees wait), atomically renamed into place
"${SSH[@]}" "$HOST" "bash -s" <<EOF
set -euo pipefail
export PATH=\$HOME/.local/bin:/usr/bin:/bin
exec 9>'$STATE/venv.lock'; flock 9
if [ ! -e '$VENV/.complete' ]; then
  rm -rf '$VENV'
  cd '$RTREE'
  UV_PROJECT_ENVIRONMENT='$VENV' uv sync -q --frozen --no-install-project --group dev --python '$PYVER'
  touch '$VENV/.complete'
  echo "test-remote: built $VENV" >&2
fi
if [ ! -x '$VPVENV/bin/python' ]; then
  uv venv -q --python 3.12 '$VPVENV' && uv pip install -q --python '$VPVENV/bin/python' zstandard==0.25.0
fi
touch '$VENV'
find '$STATE' -maxdepth 1 -name 'venv-*' -type d -mtime +14 -exec rm -rf {} +
EOF

PREFIX=""
[[ $MODE == proot ]] && PREFIX="$TOOLS/bin/proot -b $RHOME/mirror/Users:/Users"
q() { printf '%q ' "$@"; }
ENVV=(PATH="$TOOLS/bin:$VENV/bin:/usr/bin:/bin" HOME=/Users/fab LD_LIBRARY_PATH="$TOOLS/xlib" LANG=C.UTF-8 OMP_THREAD_LIMIT=1
  PYTHONPATH="$TREE/src:$TREE/tests" PYTHONDONTWRITEBYTECODE=1
  DSDIG_DATASHEET_ROOT="$DATASHEET_ROOT" DSDIG_VENDORPULL_ROOT="$VENDORPULL_ROOT"
  DSDIG_VENDORPULL_PYTHON="$VPVENV/bin/python" DSDIG_TEST_CACHE_DIR="$STATE/${DSDIG_REMOTE_CACHE:-test-cache}")
for v in DSDIG_TEST_CACHE DSDIG_PROFILE_SUBPROCESS DSDIG_TEST_CACHE_STATS; do
  [[ -n ${!v:-} ]] && ENVV+=("$v=${!v}")
done
JOB="cd $(q "$TREE") && exec env -i $(q "${ENVV[@]}") $VENV/bin/python -m pytest -p no:cacheprovider -n @SLOTS@ -m '' $(q "$@")"
# The job runs detached on the host (own session, output to a file), so a dropped connection does
# not kill it; this side streams the file and touches a heartbeat every POLL s. A watchdog on the host
# kills the job's session when the heartbeat is older than HB_TIMEOUT s (this script killed hard,
# lost for good), and Ctrl-C/SIGTERM here kills it at once.
RUN=$STATE/runs/$(date +%Y%m%d-%H%M%S)-$$
export DSDIG_REMOTE_RUN=$RUN DSDIG_REMOTE_JOB="$PREFIX bash -c $(printf '%q' "$JOB")"
export DSDIG_REMOTE_SSH="${SSH[*]}"
set +e
echo "test-remote: $(date +%T) queueing for up to $NPROC slot(s) on $HOST (at least $MINSLOTS)" >&2
# CPU work goes through the offload slot queue: up to NPROC slots on HOST, pytest -n = slots held
"$OFFLOAD" hold --tag "$TAG" -n "$NPROC" --min "$MINSLOTS" --gather "$GATHER" --host "$HOST" -- \
  env DSDIG_REMOTE_STAGE=held bash "$0"
return $?
}

held() {   # inside `offload hold`: OFFLOAD_HOST / OFFLOAD_SLOTS set, DSDIG_REMOTE_* from main()
  local LC_ALL=C   # ${#out} counts bytes
  local ssh=($DSDIG_REMOTE_SSH) run=$DSDIG_REMOTE_RUN host=$OFFLOAD_HOST off=0 out rc
  local poll=${DSDIG_REMOTE_POLL:-5} hb_timeout=${DSDIG_REMOTE_HB_TIMEOUT:-600}
  echo "test-remote: $(date +%T) $OFFLOAD_SLOTS slot(s) on $host (tag $OFFLOAD_TAG), run dir $run" >&2
  local job=${DSDIG_REMOTE_JOB//@SLOTS@/$OFFLOAD_SLOTS}
  timeout 120 "${ssh[@]}" "$host" "mkdir -p '$run' && cd '$run' && touch hb && printf '%s\n' $(printf '%q' "$job") > job.sh &&
    { setsid bash -c 'bash job.sh > out.log 2>&1; echo \$? > rc.tmp; mv rc.tmp rc' < /dev/null > /dev/null 2>&1 &
      echo \$! > pgid; } &&
    { setsid bash -c 'while [ ! -e rc ]; do
        if [ \$(( \$(date +%s) - \$(stat -c %Y hb) )) -gt $hb_timeout ]; then
          kill -TERM -- -\$(cat pgid) 2>/dev/null; sleep 5; kill -KILL -- -\$(cat pgid) 2>/dev/null
          echo \"test-remote: no heartbeat for $hb_timeout s, job killed\" >> out.log; echo 124 > rc; fi
        sleep 10; done' < /dev/null > /dev/null 2>&1 & }" || { echo "test-remote: could not start the job" >&2; return 255; }
  stop() { timeout 60 "${ssh[@]}" "$host" "kill -TERM -- -\$(cat '$run/pgid') 2>/dev/null; sleep 3; kill -KILL -- -\$(cat '$run/pgid') 2>/dev/null; echo 130 > '$run/rc'"; exit 130; }
  trap stop INT TERM HUP
  while :; do
    # one round trip: heartbeat, new output bytes, then (if finished) the exit code after a marker
    # a bounded call: from a loaded Mac an ssh to the host now and then hangs for minutes (2026-11-25)
    if out=$(timeout 30 "${ssh[@]}" "$host" "cd '$run' && touch hb && tail -c +$((off + 1)) out.log | head -c 4000000;
        if [ -e rc ]; then printf '\n@@RC@@%s' \"\$(cat rc)\"; fi" 2>/dev/null && echo x); then
      out=${out%x}
      if [[ $out == *$'\n@@RC@@'* ]]; then
        rc=${out##*@@RC@@}; out=${out%$'\n@@RC@@'*}
        printf '%s' "$out"; off=$((off + ${#out}))
        # anything written between the tail and the rc check
        timeout 60 "${ssh[@]}" "$host" "tail -c +$((off + 1)) '$run/out.log'" 2>/dev/null
        rc=${rc//[!0-9]/}
        return "${rc:-255}"
      fi
      printf '%s' "$out"; off=$((off + ${#out}))
    fi
    sleep "$poll" & wait $!
  done
}

if [[ ${DSDIG_REMOTE_STAGE:-} == held ]]; then
  held
  exit $?
fi
main "$@"
