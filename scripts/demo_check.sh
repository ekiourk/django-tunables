#!/usr/bin/env bash
# Install the built wheel into a fresh venv next to a copy of the demo project, serve it, and drive the
# embedded panels through real sessions, CSRF and redirects. Exits non-zero on the first failing step.
source "$(dirname "$0")/_wheel_env.sh"

install_wheel "${DEMO_PYTHON:-3.12}"
cp -r "$ROOT/demo" demo
rm -f demo/db.sqlite3

echo "setting up the demo database"
export DJANGO_SETTINGS_MODULE=demo.settings
"$PY" -m django migrate -v0
"$PY" -m django tunables_sync
"$PY" -m django demo_seed > /dev/null

serve / "$PY" -m django runserver

echo "driving the panels"
if ! "$PY" -I "$ROOT/scripts/demo_drive.py" "http://localhost:$PORT"; then
    fail "see the failed checks above; the server log follows:"$'\n'"$(cat server.log)"
fi
if grep -q ' 500 ' server.log; then fail "the server answered 500:"$'\n'"$(cat server.log)"; fi

echo "demo check passed: wheel installs, templates and tags are packaged, panels save, refuse and swap as documented"
