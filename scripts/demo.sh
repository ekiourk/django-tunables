#!/usr/bin/env bash
# Run the demo project from the checkout: migrate, sync the catalogue, create the users, serve on port 8000.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
export PYTHONPATH=src DJANGO_SETTINGS_MODULE=demo.settings

uv run python -m django migrate -v0
uv run python -m django tunables_sync
uv run python -m django demo_seed
echo "open http://localhost:${DEMO_PORT:-8000}/ and log in as admin, writer, viewer or nobody (password: demo)"
exec uv run python -m django runserver "${DEMO_PORT:-8000}"
