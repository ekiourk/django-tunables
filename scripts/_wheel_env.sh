# Sourced by quickstart_check.sh and demo_check.sh. Builds the wheel, installs it into a fresh venv in a
# temporary directory, serves a Django project from there and cleans up on exit.
#
#   install_wheel               # python from CHECK_PYTHON (default 3.12); sets WORK (the cwd afterwards) and PY
#   serve /path/to/probe "$PY" manage.py runserver   # sets PORT; the server logs to server.log in WORK
#   check_server_log            # fails when the server answered 500
#   fail "message"              # prints "<script> FAILED: message" and exits 1
#   fail_with_log "message"     # the same, with server.log appended
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CHECK=$(basename "$0" .sh)
WORK=$(mktemp -d)
SERVER=""
cleanup() {
    if [ -n "$SERVER" ]; then kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; fi
    rm -rf "$WORK"
}
trap cleanup EXIT

fail() { echo "$CHECK FAILED: $*" >&2; exit 1; }
fail_with_log() { fail "$1"$'\n'"$(cat server.log)"; }

install_wheel() {
    local python_version=${CHECK_PYTHON:-3.12} wheel
    echo "building wheel"
    uv build -q --out-dir "$WORK/dist" "$ROOT"
    wheel=$(ls "$WORK"/dist/*.whl)
    echo "installing $(basename "$wheel") into a fresh venv (python $python_version)"
    cd "$WORK"
    uv venv -q --python "$python_version" .venv
    uv pip install -q --python .venv/bin/python "$wheel"
    PY=$WORK/.venv/bin/python
}

serve() {
    local probe=$1 tries=0 url
    shift
    PORT=$("$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
    "$@" "$PORT" --noreload > server.log 2>&1 &
    SERVER=$!
    url="http://localhost:$PORT$probe"
    until curl -s -o /dev/null "$url"; do
        (( tries++ < 50 )) || fail_with_log "server did not answer at $url:"
        sleep 0.2
    done
}

check_server_log() {
    if grep -q ' 500 ' server.log; then fail_with_log "the server answered 500:"; fi
}
