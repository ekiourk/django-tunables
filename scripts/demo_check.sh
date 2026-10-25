#!/usr/bin/env bash
# Install the built wheel into a fresh venv next to a copy of the demo project, serve it, and drive the
# embedded panels through real sessions, CSRF and redirects. Exits non-zero on the first failing step.
source "$(dirname "$0")/_wheel_env.sh"

install_wheel
cp -r "$ROOT/demo" demo
rm -rf demo/db.sqlite3 demo/__pycache__

echo "setting up the demo database"
export DJANGO_SETTINGS_MODULE=demo.settings
"$PY" -m django demo_setup

serve / "$PY" -m django runserver

echo "driving the panels"
"$PY" -I "$ROOT/scripts/demo_drive.py" "http://localhost:$PORT" || fail_with_log "see the failed checks above; the server log follows:"
check_server_log

echo "demo check passed: wheel installs, templates and tags are packaged, panels save, refuse and swap as documented"
