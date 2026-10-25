#!/usr/bin/env bash
# Install the built wheel into a fresh venv next to a copy of the demo project, serve it, and drive the
# embedded panels through real sessions, CSRF and redirects. Exits non-zero on the first failing step.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON_VERSION=${DEMO_PYTHON:-3.12}
WORK=$(mktemp -d)
SERVER=""
cleanup() {
    if [ -n "$SERVER" ]; then kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; fi
    rm -rf "$WORK"
}
trap cleanup EXIT

fail() { echo "demo check FAILED: $*" >&2; exit 1; }

echo "building wheel"
uv build -q --out-dir "$WORK/dist" "$ROOT"
WHEEL=$(ls "$WORK"/dist/*.whl)

echo "installing $(basename "$WHEEL") into a fresh venv (python $PYTHON_VERSION)"
cd "$WORK"
uv venv -q --python "$PYTHON_VERSION" .venv
uv pip install -q --python .venv/bin/python "$WHEEL"
PY=$WORK/.venv/bin/python
cp -r "$ROOT/demo" demo
rm -f demo/db.sqlite3

echo "setting up the demo database"
export DJANGO_SETTINGS_MODULE=demo.settings
"$PY" -m django migrate -v0
"$PY" -m django tunables_sync -v0
"$PY" -m django demo_seed > /dev/null

PORT=$($PY -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
"$PY" -m django runserver "$PORT" --noreload > server.log 2>&1 &
SERVER=$!
for _ in $(seq 1 50); do
    curl -s -o /dev/null "http://localhost:$PORT/" && break
    sleep 0.2
done
curl -s -o /dev/null "http://localhost:$PORT/" || fail "server did not answer on port $PORT:"$'\n'"$(cat server.log)"

echo "driving the panels"
"$PY" -I "$ROOT/scripts/demo_drive.py" "http://localhost:$PORT" || fail "see the failed checks above"
if grep -q ' 500 ' server.log; then fail "the server answered 500:"$'\n'"$(cat server.log)"; fi

echo "demo check passed: wheel installs, templates and tags are packaged, panels save, refuse and swap as documented"
