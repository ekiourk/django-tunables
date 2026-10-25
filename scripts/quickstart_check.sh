#!/usr/bin/env bash
# Install the built wheel into a fresh Django project and run the README quickstart literally.
# Exits non-zero with a message on the first step that does not behave as the README says.
source "$(dirname "$0")/_wheel_env.sh"

blocks() { python3 "$ROOT/scripts/readme_blocks.py" "$@"; }

install_wheel "${QUICKSTART_PYTHON:-3.12}"

"$WORK/.venv/bin/django-admin" startproject myproject .
blocks catalogue > myproject/tunables_catalogue.py
blocks urls > myproject/urls.py
# The settings block shows INSTALLED_APPS with a placeholder; take the app names and the TUNABLES line from it.
{
    echo
    echo "INSTALLED_APPS += $(blocks settings | grep -oE '^\s+"[a-z_]+",' | tr -d ' ,' | paste -sd, | sed 's/^/[/; s/$/]/')"
    blocks settings | grep '^TUNABLES'
} >> myproject/settings.py

echo "running the setup commands"
blocks setup | sed "s|^python |$PY |" | bash

serve /api/tunables/values/ "$PY" manage.py runserver

echo "running the README requests"
run_block() { blocks "$1" | sed "s|localhost:8000|localhost:$PORT|g" | bash; }
READ=$(run_block read)
CHANGE=$(run_block change)

"$PY" - "$READ" "$CHANGE" <<'PYCHECK' || exit 1
import json, sys
read, change = (json.loads(arg) for arg in sys.argv[1:3])
problems = []
if read.get("version") != 0: problems.append(f"values/ version {read.get('version')!r}, expected 0")
if read.get("groups", {}).get("pricing", {}).get("vat_rate") != 0.24: problems.append("values/ vat_rate is not the default 0.24")
if read.get("overridden") != []: problems.append(f"values/ overridden {read.get('overridden')!r}, expected []")
if change.get("version") != 1: problems.append(f"changesets/ version {change.get('version')!r}, expected 1: {json.dumps(change)[:200]}")
cs = change.get("changeset", {})
if (cs.get("actor"), cs.get("actor_source")) != ("alice", "asserted"): problems.append(f"actor {cs.get('actor')!r}/{cs.get('actor_source')!r}, expected alice/asserted")
if cs.get("reason") != "autumn rate": problems.append(f"reason {cs.get('reason')!r}")
if problems:
    print("quickstart check FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
    sys.exit(1)
PYCHECK

SHOW=$("$PY" manage.py tunables_show)
grep -qx 'pricing.vat_rate = 0.2  (override)' <<<"$SHOW" || fail "tunables_show did not mark the override:"$'\n'"$SHOW"
ADMIN=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "http://localhost:$PORT/admin/tunables/tunabledefinition/")
[[ "$ADMIN" == 302\ *"/admin/login/"* ]] || fail "admin index did not redirect to login: $ADMIN"
if grep -qi traceback server.log; then fail "server log has a traceback:"$'\n'"$(cat server.log)"; fi

echo "quickstart check passed: wheel installs, README steps work, version 1 written by alice, admin reachable"
