#!/usr/bin/env bash
# Run the demo project from the checkout: migrate, sync the catalogue, create the users, serve on port 8000.
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH=src DJANGO_SETTINGS_MODULE=demo.settings

uv run python -m django demo_setup
echo "open http://localhost:${DEMO_PORT:-8000}/ and log in as admin, writer, viewer or nobody (password: demo)"
exec uv run python -m django runserver "${DEMO_PORT:-8000}"
