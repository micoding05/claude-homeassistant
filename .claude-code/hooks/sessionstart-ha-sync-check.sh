#!/bin/bash
# SessionStart hook: reconcile the Home Assistant server with this checkout.
#
# Runs the cheap deterministic checks and injects the result as context, so work
# never starts against a stale baseline or a validator that is out of step with
# the server's storage schema.

set -uo pipefail

cat >/dev/null 2>&1 || true

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root" || exit 0

[ -f "tools/ha_sync_check.py" ] || exit 0

if [ -x "venv/bin/python3" ]; then
    python_bin="venv/bin/python3"
elif [ -x ".venv/bin/python3" ]; then
    python_bin=".venv/bin/python3"
else
    python_bin="python3"
fi

output=$("$python_bin" tools/ha_sync_check.py 2>&1)
status=$?

# In sync: stay quiet so a clean session start costs nothing.
[ $status -eq 0 ] && exit 0

if [ $status -eq 2 ]; then
    note="The Home Assistant server is unreachable, so only local checks ran. \
Do not assume the local config matches the server."
else
    note="The local checkout differs from the Home Assistant server. Resolve \
this before editing any YAML - especially a homeassistant package version that \
does not match the server, which makes the official validator fail on newer \
.storage schemas. Launch the ha-sync-check agent if the drift needs untangling."
fi

jq -n --arg out "$output" --arg note "$note" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: ($note + "\n\n" + $out)}}'
exit 0
