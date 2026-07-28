#!/usr/bin/env bash
# Stop hook: concurrency-safe auto-commit + push for multiple parallel agents.
#
# Several Claude Code sessions share one working tree and this one hook. All the
# concurrency handling (atomic lock, "nothing to commit" is fine, push-race
# reconcile with pull --rebase) lives in the shared git-sync.sh so that agents
# committing mid-turn (e.g. the deploy skill) get the exact same safe behaviour.
# This wrapper just adds the session tag and surfaces a status to the user.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# session id (from the Stop hook's stdin JSON): full id selects THIS agent's
# manifest so git-sync commits only our files; 8-char is the human commit tag.
payload="$(cat 2>/dev/null || true)"
session_full="$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -n "$session_full" ] && session_full="${session_full:-${CLAUDE_CODE_SESSION_ID:-}}"
session_tag="$(printf '%s' "$session_full" | cut -c1-8)"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
out="$("$DIR/git-sync.sh" "" "$session_tag" "$session_full" 2>/dev/null || true)"

case "$out" in
  *"pushed to"*)   printf '{"systemMessage":"✅ Auto-committed & pushed — Railway deploy triggered"}\n' ;;
  *"nothing to commit"*) : ;;  # a sibling agent handled it — stay quiet
  *"push deferred"*) printf '{"systemMessage":"⚠️ Auto-committed locally; push will reconcile on the next turn."}\n' ;;
  *) : ;;
esac
exit 0
