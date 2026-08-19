#!/bin/bash
# PreToolUse hook: block pushes and PR creation that would leak private data.
#
# This repository is a PUBLIC GitHub fork. Anything pushed is world-readable and
# stays in the history forever, so outgoing changes get scanned first.
#
# Reads the PreToolUse payload on stdin and emits a permission decision as JSON.

set -uo pipefail

payload=$(cat)
command=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null)

[ -z "$command" ] && exit 0

# Match the publishing commands at the start of the line or after a shell
# operator, so a compound command still triggers but a commit message that
# merely mentions "git push" does not.
if ! printf '%s' "$command" | grep -Eq '(^|[;&|]|&&|\|\|)[[:space:]]*(git[[:space:]]+push|gh[[:space:]]+pr[[:space:]]+create|glab[[:space:]]+mr[[:space:]]+create)'; then
    exit 0
fi

# A dry run publishes nothing.
if printf '%s' "$command" | grep -Eq '(--dry-run|--help|-h$)'; then
    exit 0
fi

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root" || exit 0

[ -f "tools/privacy_scan.py" ] || exit 0

if [ -x "venv/bin/python3" ]; then
    python_bin="venv/bin/python3"
elif [ -x ".venv/bin/python3" ]; then
    python_bin=".venv/bin/python3"
else
    python_bin="python3"
fi

scan_output=$("$python_bin" tools/privacy_scan.py 2>&1)
scan_status=$?

if [ $scan_status -eq 0 ]; then
    # Clean: allow the push, but tell the model the deep review still matters.
    jq -n --arg ctx "Privacy scan found no private data in the outgoing changes. \
The pattern scan cannot judge context (household-identifying text, hardware \
inventory, presence patterns) - launch the privacy-guard agent if this push \
adds substantial new content." \
        '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "allow", additionalContext: $ctx}}'
    exit 0
fi

# Findings: refuse the push and hand the details back to the model.
reason="🚫 Privacy scan BLOCKED this push - private data would become public.

${scan_output}

This repo is a public GitHub fork; pushed data stays in history forever.
Fix every BLOCK finding, then launch the privacy-guard agent to review the
diff for context-dependent leaks the pattern scan cannot catch."

jq -n --arg reason "$reason" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
exit 0
