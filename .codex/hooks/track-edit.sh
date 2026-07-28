#!/usr/bin/env bash
# PostToolUse hook (Edit|Write|MultiEdit|NotebookEdit): record every file THIS
# agent session touches into a per-session manifest. git-sync then commits ONLY
# these files, so one agent's commit can never sweep in a sibling agent's
# half-finished / non-building work — and nothing gets lost.
#
#   manifest: <git-dir>/agent-manifests/<session_id>.list   (repo-relative paths)
#
# .git/ is never tracked, so the manifests are private per checkout.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
proj="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$proj" 2>/dev/null || exit 0
gitdir="$(git rev-parse --git-dir 2>/dev/null)" || exit 0

# session id + edited path from the tool JSON (jq if present, sed fallback)
if command -v jq >/dev/null 2>&1; then
  sid="$(printf '%s' "$payload" | jq -r '.session_id // empty' 2>/dev/null)"
  fp="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)"
else
  sid="$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  fp="$(printf '%s' "$payload" | sed -n 's/.*"\(file_path\|notebook_path\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\2/p' | head -1)"
fi
[ -n "$sid" ] && [ -n "$fp" ] || exit 0

# store repo-relative; ignore anything outside the project (scratchpad, /tmp, …)
case "$fp" in
  "$proj"/*) rel="${fp#"$proj"/}" ;;
  /*)        exit 0 ;;
  *)         rel="$fp" ;;
esac

mdir="$gitdir/agent-manifests"
mkdir -p "$mdir" 2>/dev/null || exit 0
mf="$mdir/$sid.list"
touch "$mf" 2>/dev/null || exit 0
grep -qxF -- "$rel" "$mf" 2>/dev/null || printf '%s\n' "$rel" >> "$mf"
exit 0
