# Sourced by quickstart_check.sh and demo_check.sh. Builds the wheel, installs it into a fresh venv in a
# temporary directory, serves a Django project from there and cleans up on exit.
#
#   install_wheel 3.12          # sets WORK (the cwd afterwards), WHEEL and PY
#   serve /path/to/probe "$PY" manage.py runserver   # sets PORT and SERVER; writes server.log in WORK
#   fail "message"              # prints "<script> FAILED: message" and exits 1
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

install_wheel() {
    local python_version=$1
    echo "building wheel"
    uv build -q --out-dir "$WORK/dist" "$ROOT"
    WHEEL=$(ls "$WORK"/dist/*.whl)
    echo "installing $(basename "$WHEEL") into a fresh venv (python $python_version)"
    cd "$WORK"
    uv venv -q --python "$python_version" .venv
    uv pip install -q --python .venv/bin/python "$WHEEL"
    PY=$WORK/.venv/bin/python
}

serve() {
    local probe=$1
    shift
    PORT=$("$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
    "$@" "$PORT" --noreload > server.log 2>&1 &
    SERVER=$!
    for _ in $(seq 1 50); do
        curl -s -o /dev/null "http://localhost:$PORT$probe" && break
        sleep 0.2
    done
    curl -s -o /dev/null "http://localhost:$PORT$probe" || fail "server did not answer on port $PORT:"$'\n'"$(cat server.log)"
}
